# Einmaliger Umzug zu Nuki Direkt 0.1.0

Ab 0.1.0 heißen Domain und Komponentenordner `nuki_direkt`, das HACS-Archiv
`nuki_direkt.zip`. Der Anzeigename bleibt **Nuki Direkt**. Die Umbenennung betrifft
auch vorhandene Home-Assistant-Registrierungen; ein HACS-Download allein genügt
nicht. Der bisherige technische Name `hass_nuki_bt` erscheint hier nur zur
Erkennung der Ausgangsinstallation.

**Geräte nicht löschen und Schlösser nicht neu koppeln.** Das Werkzeug übernimmt
bestehende Config-Entry-IDs, Kopplungsdaten, Optionen, Entity-IDs, Unique-IDs und
Device-IDs. Dadurch bleiben darauf verweisende Automationen erhalten. Es sendet
keine Bluetooth- oder Motorbefehle.

## Voraussetzungen

- Ein aktuelles, geschütztes Home-Assistant-Backup einschließlich `.storage`
  und des bisherigen Komponentenordners.
- Zugriff auf das Konfigurationsverzeichnis und den Docker-Daemon von Home
  Assistant OS oder einer Container-Installation; Python 3.10 oder neuer.
- Keine parallel neu angelegten Einträge für dasselbe Schloss. Konflikte müssen
  vor dem Umzug geprüft werden.
- Individuelle Konfigurationen nach der alten Kennung durchsuchen. Direkte
  Integrationsfilter oder eigene Domain-Verweise müssen angepasst werden.
  Normale Verweise auf bestehende Entity-IDs und Device-IDs bleiben gültig.

## Ablauf

1. Das Werkzeug `tools/migrate_nuki_direkt.py` aus diesem Release herunterladen.
2. Vorprüfung ausführen; ohne `--apply` wird nichts verändert:

   ```sh
   python3 migrate_nuki_direkt.py --config /config
   ```

3. Nuki Direkt 0.1.0 in HACS herunterladen. Noch nicht neu starten und keine
   neuen Geräte hinzufügen. Prüfen, dass der neue Komponentenordner vorhanden ist.
4. **Home Assistant Core vollständig stoppen**, bei HA OS über den Supervisor.
   Der SSH-Zugang muss danach weiterhin verfügbar sein. Kein Host-Neustart.
5. Umzug ausführen:

   ```sh
   python3 migrate_nuki_direkt.py --config /config --apply
   ```

   Das Werkzeug prüft über Docker, dass der Container `homeassistant` gestoppt
   ist. Bei anderem Containernamen `--core-container NAME` angeben. Es sichert
   die drei Registries unter `/config/nuki_direkt_backups/`, ändert ausschließlich
   deren Integrationszuordnung und verschiebt den alten Komponentenordner in
   die Sicherung. Die Dateien enthalten vertrauliche Kopplungsdaten und dürfen
   nicht veröffentlicht werden. Bei Fehlern Core gestoppt lassen und Ursache
   sowie Sicherung prüfen.
6. Core starten. HACS-Version, geladene Geräte, unveränderte Entity-IDs,
   Schlossstatus und eigene Automationen kontrollieren. Den alten Komponentenordner
   nicht wieder parallel installieren.

Die Sicherung nach erfolgreicher Abnahme geschützt aufbewahren. Ein erneuter
Werkzeugaufruf auf einer vollständig umgezogenen Installation ändert nichts.
Die Commit-Historie und rechtlichen Herkunftshinweise bleiben erhalten.

## Rückweg

Core erneut vollständig stoppen. Die vor dem Umzug gesicherten Registries und
den alten Komponenten-Code gemeinsam wiederherstellen, den neuen Komponentenordner
aus dem aktiven Verzeichnis nehmen und Core starten. Diesen unmittelbaren Rückweg
nur verwenden, solange keine neueren Konfigurationsänderungen entstanden sind;
sonst den Rückumzug gezielt planen. Alte Registries niemals pauschal über neuere
Änderungen zurückspielen. Anschließend HACS auf den passenden Versionsstand bringen.
