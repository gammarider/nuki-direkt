"""Offline registry migration to Nuki Direkt; never print pairing credentials."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

OLD = "hass_nuki_bt"
NEW = "nuki_direkt"
FILES = ("core.config_entries", "core.entity_registry", "core.device_registry")


def plan(documents):
    """Change only integration ownership; preserve IDs, settings and secrets."""
    result = deepcopy(documents)
    entries = result[FILES[0]]["data"]["entries"]
    migrating = {e["entry_id"] for e in entries if e["domain"] == OLD}
    target = [e for e in entries if e["domain"] == NEW]
    old_entries = [e for e in entries if e["entry_id"] in migrating]
    for old in old_entries:
        for new in target:
            same_id = old.get("unique_id") is not None and old.get("unique_id") == new.get("unique_id")
            address = str(old["data"].get("device_address", "")).strip().upper()
            same_address = address and address == str(new["data"].get("device_address", "")).strip().upper()
            if same_id or same_address:
                raise ValueError("Conflicting old and new entries; resolve the duplicate before migration")
    for entry in old_entries:
        entry["domain"] = NEW
    entity_count = 0
    registry = result[FILES[1]]["data"]
    for name in ("entities", "deleted_entities"):
        entities = registry.get(name, [])
        target_ids = {(e["entity_id"].split(".")[0], e["unique_id"]) for e in entities if e.get("platform") == NEW}
        for entity in entities:
            if entity.get("platform") == OLD:
                if name == "entities" and entity.get("config_entry_id") not in migrating:
                    raise ValueError("Old entity does not belong to a migrating entry")
                if (entity["entity_id"].split(".")[0], entity["unique_id"]) in target_ids:
                    raise ValueError("Entity identity collision after migration")
                entity["platform"] = NEW
                entity_count += 1
    device_count = 0
    target_ids = {tuple(i) for d in result[FILES[2]]["data"]["devices"] for i in d.get("identifiers", []) if i[0] == NEW}
    for device in result[FILES[2]]["data"]["devices"]:
        identifiers = device.get("identifiers", [])
        for identifier in identifiers:
            if identifier[0] == OLD:
                if (NEW, identifier[1]) in target_ids:
                    raise ValueError("Device identifier collision after migration")
                identifier[0] = NEW
                device_count += 1
    return result, {"entries": len(migrating), "entities": entity_count, "device_identifiers": device_count}


def assert_core_stopped(container):
    """Fail closed unless Docker confirms the named Core container is stopped."""
    output = subprocess.check_output(
        ["docker", "inspect", "--format", "{{.State.Status}}", container], text=True,
    ).strip()
    if output not in ("exited", "created"):
        raise RuntimeError("Home Assistant Core must remain stopped for the entire migration")


def atomic_write(path, data):
    """Replace one registry with private permissions on the same filesystem."""
    fd, temporary = tempfile.mkstemp(prefix=".nuki-migrate-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            os.fchmod(file.fileno(), 0o600)
            stat = path.stat()
            if os.geteuid() == 0:
                os.fchown(file.fileno(), stat.st_uid, stat.st_gid)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def migrate(config, *, apply=False, container="homeassistant"):
    """Preflight by default; apply only with stopped Core and a full backup."""
    config = Path(config)
    storage = config / ".storage"
    if apply:
        assert_core_stopped(container)
        manifest = json.loads((config / "custom_components" / NEW / "manifest.json").read_text())
        if manifest["domain"] != NEW:
            raise ValueError("Install the new Nuki Direkt component before migration")
    originals = {name: (storage / name).read_bytes() for name in FILES}
    documents = {name: json.loads(data) for name, data in originals.items()}
    result, counts = plan(documents)
    changed = [name for name in FILES if result[name] != documents[name]]
    legacy = config / "custom_components" / OLD
    if not apply or (not changed and not legacy.exists()):
        return {**counts, "applied": False, "changed_files": len(changed)}
    backup_root = config / "nuki_direkt_backups"
    backup_root.mkdir(mode=0o700, exist_ok=True)
    backup_root.chmod(0o700)
    backup = backup_root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup.mkdir(mode=0o700)
    for name, data in originals.items():
        p = backup / name
        p.write_bytes(data)
        p.chmod(0o600)
    moved = False
    written = []
    try:
        for name in changed:
            assert_core_stopped(container)
            # Detect concurrent writes instead of overwriting unexpected changes.
            if (storage / name).read_bytes() != originals[name]:
                raise RuntimeError("Registry changed during migration")
            atomic_write(storage / name, (json.dumps(result[name], ensure_ascii=False, indent=2) + "\n").encode())
            written.append(name)
        assert_core_stopped(container)
        if legacy.exists():
            shutil.move(str(legacy), str(backup / "previous_component"))
            moved = True
        for name in FILES:
            if json.loads((storage / name).read_bytes()) != result[name]:
                raise RuntimeError("Registry verification failed")
    except Exception:
        # Never overwrite an active Core's files during rollback either.
        assert_core_stopped(container)
        for name in written:
            atomic_write(storage / name, originals[name])
        if moved:
            shutil.move(str(backup / "previous_component"), str(legacy))
        raise
    return {**counts, "applied": True, "changed_files": len(changed), "backup": str(backup)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--core-container", default="homeassistant")
    args = parser.parse_args()
    try:
        result = migrate(args.config, apply=args.apply, container=args.core_container)
    except Exception as ex:
        # Paths and schema errors may contain private content; print no payloads.
        parser.exit(1, f"Migration stopped ({type(ex).__name__}). Keep Core stopped; check configuration, conflicts and backups.\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
