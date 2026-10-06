"""Offline migration invariants and failure recovery with synthetic registries."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import migrate_nuki_direkt as migration


def fixture():
    return {
        migration.FILES[0]: {'version':1, 'data': {'entries': [
            {'entry_id':'original-entry', 'domain':migration.OLD, 'unique_id':'aabb',
             'data':{'device_address':'AA:BB:CC:DD:EE:FF','private_key':'synthetic-private-key','pin':'0042'},
             'options':{'status_reconnect':False}, 'disabled_by':None, 'title':'Original name'},
            {'entry_id':'other', 'domain':'other', 'data':{'keep':'unchanged'}}]}},
        migration.FILES[1]: {'version':1, 'data': {'entities': [
            {'entity_id':'lock.original_name','platform':migration.OLD,'unique_id':'aabb-lock',
             'config_entry_id':'original-entry','device_id':'original-device','disabled_by':None,
             'name':'Custom name','area_id':'hall','labels':['security']},
            {'entity_id':'sensor.other','platform':'other','unique_id':'other','config_entry_id':'other'}],
            'deleted_entities':[
                {'entity_id':'sensor.old_name','platform':migration.OLD,'unique_id':'aabb-old','config_entry_id':'original-entry'}]}},
        migration.FILES[2]: {'version':1, 'data': {'devices': [
            {'id':'original-device','config_entry_id':'original-entry',
             'identifiers':[[migration.OLD,'aabb']], 'connections':[['bluetooth','AA:BB:CC:DD:EE:FF']],
             'area_id':'hall','name_by_user':'Custom device'},
            {'id':'other-device','identifiers':[['other','different']]}]}},
    }


class PlanTests(unittest.TestCase):
    def test_only_ownership_changes(self):
        documents = fixture()
        before = deepcopy(documents)
        result, counts = migration.plan(documents)
        self.assertEqual(documents, before)
        self.assertEqual(counts, {'entries':1, 'entities':2,'device_identifiers':1})
        result[migration.FILES[0]]['data']['entries'][0]['domain'] = migration.OLD
        result[migration.FILES[1]]['data']['entities'][0]['platform'] = migration.OLD
        result[migration.FILES[1]]['data']['deleted_entities'][0]['platform'] = migration.OLD
        result[migration.FILES[2]]['data']['devices'][0]['identifiers'][0][0] = migration.OLD
        self.assertEqual(result, before)

    def test_unrelated_composite_device_identifiers_are_preserved(self):
        docs=fixture()
        docs[migration.FILES[2]]['data']['devices'].append({'id':'other-composite','identifiers':[['other','different']]})
        result, _ = migration.plan(docs)
        self.assertEqual(result[migration.FILES[2]]['data']['devices'][1:], docs[migration.FILES[2]]['data']['devices'][1:])

    def test_second_plan_is_noop(self):
        first, _ = migration.plan(fixture())
        second, counts = migration.plan(first)
        self.assertEqual(first, second)
        self.assertFalse(any(counts.values()))

    def test_existing_target_address_conflict_fails(self):
        docs=fixture()
        docs[migration.FILES[0]]['data']['entries'].append({
            'entry_id':'duplicate', 'domain':migration.NEW, 'unique_id':None,
            'data':{'device_address':'aa:bb:cc:dd:ee:ff'}})
        with self.assertRaises(ValueError):migration.plan(docs)

    def test_entity_collision_fails(self):
        docs=fixture()
        docs[migration.FILES[1]]['data']['entities'].append({
            'entity_id':'lock.duplicate','platform':migration.NEW,'unique_id':'aabb-lock','config_entry_id':'new'})
        with self.assertRaises(ValueError):migration.plan(docs)

    def test_foreign_entity_is_not_silently_migrated(self):
        docs=fixture()
        docs[migration.FILES[1]]['data']['entities'][0]['config_entry_id']='other'
        with self.assertRaises(ValueError):migration.plan(docs)

    def test_device_collision_fails(self):
        docs=fixture()
        docs[migration.FILES[2]]['data']['devices'].append({'id':'duplicate','identifiers':[[migration.NEW,'aabb']]})
        with self.assertRaises(ValueError):migration.plan(docs)


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        (self.root/'.storage').mkdir()
        for name,data in fixture().items():
            (self.root/'.storage'/name).write_text(json.dumps(data))
        new=self.root/'custom_components'/migration.NEW
        new.mkdir(parents=True)
        (new/'manifest.json').write_text(json.dumps({'domain':migration.NEW,'version':'0.1.1'}))
        old=self.root/'custom_components'/migration.OLD
        old.mkdir()
        (old/'previous.py').write_text('previous code')
        self.originals={n:(self.root/'.storage'/n).read_bytes() for n in migration.FILES}

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_does_not_write_or_require_docker(self):
        with patch.object(migration,'assert_core_stopped') as guard:
            result=migration.migrate(self.root)
        guard.assert_not_called()
        self.assertFalse(result['applied'])
        self.assertFalse((self.root/'nuki_direkt_backups').exists())
        self.assertEqual(self.originals,{n:(self.root/'.storage'/n).read_bytes() for n in migration.FILES})

    def test_running_core_refuses_all_changes(self):
        with patch.object(migration.subprocess,'check_output',return_value='running\n'):
            with self.assertRaises(RuntimeError):migration.migrate(self.root,apply=True)
        self.assertFalse((self.root/'nuki_direkt_backups').exists())

    def test_apply_backs_up_preserves_secrets_and_retires_old_component(self):
        with patch.object(migration,'assert_core_stopped'):
            result=migration.migrate(self.root,apply=True)
            second=migration.migrate(self.root,apply=True)
        self.assertTrue(result['applied'])
        self.assertFalse(second['applied'])
        backup=Path(result['backup'])
        self.assertEqual(backup.stat().st_mode & 0o777,0o700)
        for name,original in self.originals.items():
            self.assertEqual((backup/name).read_bytes(),original)
            self.assertEqual((backup/name).stat().st_mode & 0o777,0o600)
        self.assertFalse((self.root/'custom_components'/migration.OLD).exists())
        self.assertTrue((backup/'previous_component/previous.py').exists())
        entry=json.loads((self.root/'.storage'/migration.FILES[0]).read_bytes())['data']['entries'][0]
        self.assertEqual(entry['data'],fixture()[migration.FILES[0]]['data']['entries'][0]['data'])

    def test_hacs_caches_move_only_the_matching_repository(self):
        target={'full_name':'gammarider/nuki-direkt','domain':migration.OLD,
                'version_installed':'0.0.25','installed':True,
                'repository_manifest':{'filename':'hass_nuki_bt.zip','zip_release':True}}
        other={'full_name':'someone/other','domain':'other','version_installed':'2.0'}
        for name in migration.HACS_FILES:
            (self.root/'.storage'/name).write_text(json.dumps({'data':[target,other]}))
        with patch.object(migration,'assert_core_stopped'):
            result=migration.migrate(self.root,apply=True)
            second=migration.migrate(self.root,apply=True)
        self.assertEqual(result['hacs_records'],2)
        self.assertFalse(second['applied'])
        for name in migration.HACS_FILES:
            records=json.loads((self.root/'.storage'/name).read_text())['data']
            self.assertEqual(records[1],other)
            self.assertEqual(records[0]['domain'],migration.NEW)
            self.assertEqual(records[0]['version_installed'],'0.1.1')
            self.assertEqual(records[0]['repository_manifest']['filename'],'nuki_direkt.zip')
            self.assertTrue((Path(result['backup'])/name).exists())

    def test_mid_write_failure_rolls_back_completed_writes(self):
        real_write=migration.atomic_write
        calls=0
        def failing(path,data):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('synthetic disk failure')
            real_write(path,data)
        with patch.object(migration,'assert_core_stopped'),patch.object(migration,'atomic_write',side_effect=failing):
            with self.assertRaises(OSError):migration.migrate(self.root,apply=True)
        self.assertEqual(self.originals,{n:(self.root/'.storage'/n).read_bytes() for n in migration.FILES})
        self.assertTrue((self.root/'custom_components'/migration.OLD).exists())

    def test_missing_new_component_refuses_writes(self):
        (self.root/'custom_components'/migration.NEW/'manifest.json').unlink()
        with patch.object(migration,'assert_core_stopped'):
            with self.assertRaises(ValueError):migration.migrate(self.root,apply=True)
        self.assertFalse((self.root/'nuki_direkt_backups').exists())
