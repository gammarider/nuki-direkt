# Nuki Direkt

**Dein Nuki-Schloss direkt in Home Assistant — über Bluetooth, ohne Nuki Bridge.**

Nuki Direkt ist eine eigenständig gepflegte, inoffizielle Integration für Nuki.
Sie verbindet Home Assistant lokal mit dem Schloss und enthält zusätzliche
Mechanismen zur Wiederherstellung unterbrochener Bluetooth-Verbindungen.
Das Projekt steht in keiner Verbindung zum Hersteller Nuki.

## Was du brauchst

- Home Assistant ab **2026.9.0** und HACS.
- Einen von Home Assistant unterstützten Bluetooth-Adapter oder Bluetooth-Proxy
  mit aktiven Verbindungen in Reichweite des Schlosses.
- Ein kompatibles Nuki-Gerät mit Bluetooth-Schnittstelle. Die bisherigen
  Hardwaretests erfolgten mit Smart Locks; Opener und weitere Gerätetypen sind
  nicht hardwareseitig abgenommen. Eine pauschale Unterstützung aller Modelle
  oder Firmwarestände wird nicht zugesagt.

## Installation

Bei einer vorhandenen Installation zuerst den Abschnitt zum Update auf 0.1.1 lesen.

1. In HACS das Menü **Benutzerdefinierte Repositories** öffnen.
2. `https://github.com/gammarider/nuki-direkt` mit Typ **Integration** hinzufügen.
3. **Nuki Direkt** herunterladen und Home Assistant neu starten.
4. Unter **Einstellungen → Geräte & Dienste** das gefundene Nuki konfigurieren
   oder **Integration hinzufügen → Nuki Direkt** auswählen.
5. Für ein neues Schloss die Kopplungsanweisungen befolgen.

Bei der Erstkopplung kann der Client-Typ **Bridge** eine bestehende Nuki-Bridge-
Registrierung ersetzen. **App** erlaubt den parallelen Betrieb; mehrere Clients
können sich beim Empfang von Aktualisierungen beeinflussen.

## Geführte Kopplung ab 0.0.25

Der Assistent prüft Adresse und PIN und verhindert doppelte Einträge. **App** ist
voreingestellt. Erst nach deiner Bestätigung beginnt ein einzelner Koppelversuch
mit Fortschrittsanzeige. Bluetooth-Aufbau, Kopplung und Aufräumen haben begrenzte
Wartezeiten. Ein Ultra verlangt vor dem Koppeln die Administrations-PIN aus der
Nuki-App; das ist nicht der Keypad-Code.

Bei Verbindungsfehlern oder Abbruch wird die Bluetooth-Verbindung freigegeben.
Autorisierungsnachrichten werden nicht automatisch wiederholt. Wenn das Schloss
bereits eine Berechtigung angelegt haben könnte und die Zugangsdaten vorliegen,
bietet der Assistent eine reine Statusprüfung dieser Berechtigung an. Dabei wird
das Schloss nicht bewegt und nicht erneut gekoppelt. Fehlen die nötigen Daten,
bricht der Assistent mit einem Hinweis zur Prüfung in der Nuki-App ab. Nach
Schließen des Assistenten oder HA-Neustart stehen unbestätigte Zugangsdaten nicht
mehr für diese Wiederaufnahme bereit. Bestehende Berechtigungen anderer Geräte
nicht löschen.

Der manuelle Import prüft Schlüssel, Schlüsselpaar und App-ID auf gültiges Format;
er führt keine Bluetooth-Verbindungsprüfung aus. Die neuen Abläufe sind mit
Home Assistants Flow-Manager und simulierten Transportfehlern getestet. Eine
neue physische Erstkopplung ist noch separat zu erproben.

## Update auf 0.1.1: einmaliger Umzug

**Ab 0.1.1 lautet die technische Kennung `nuki_direkt`.** Auch Komponentenordner
und Installationsarchiv verwenden diesen Namen. Bestehende Installationen mit
der alten Kennung benötigen den [einmaligen Umzug](docs/migration.md), bevor
Home Assistant mit dem neuen Code startet. Ein HACS-Download allein überträgt
keine vorhandenen Geräteeinträge.

Der Umzug übernimmt Kopplungsdaten, Geräteeinträge und Entitäten. Es ist kein
neues Anlernen nötig. **Vorhandene Geräte nicht löschen oder erneut koppeln.**
Nach dem Umzug darf nur die neue Komponente installiert bleiben.

## Bluetooth-Verbindung

Seit 0.0.22 ist die Wiederherstellung von Status- und Folgeabfragen standardmäßig
aktiv. Eine ausdrücklich ausgeschaltete Option bleibt ausgeschaltet. Unter
**Einstellungen → Geräte & Dienste → Nuki Direkt → Konfigurieren** lässt sie sich
je Gerät ändern; dabei wird nur dieser Geräteeintrag neu geladen.

Nach einer bestätigten Motoraktion wird für die folgende Status- oder
Challenge-Abfrage eine frische Verbindung verwendet. Seit 0.0.23 müssen beide
Bluetooth-Benachrichtigungsabonnements erfolgreich eingerichtet sein, bevor ein
Client für Befehle wiederverwendet wird. Fehlgeschlagene Verbindungen werden
mit begrenzter Wartezeit aufgeräumt. Es werden **keine zusätzlichen Motorbefehle
wiederholt**, keine globalen Timeouts verkürzt und keine Ereignisprotokolle
abgeschaltet. Funkstörungen oder ausgelastete Bluetooth-Proxys können weiterhin
Verzögerungen verursachen.

Seit 0.0.25 ist der Kopplungsassistent überarbeitet; 0.1.1 vereinheitlicht die
technische Kennung. `pyNukiBT==0.0.20` bleibt bewusst festgelegt.

## Entwicklung

Python 3.14 verwenden:

```sh
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
```

Die Tests verwenden auch Home Assistants echten Flow-Manager, simulieren die
Bluetooth-Antworten und betätigen keine echten Schlösser.
Die Release-Prüfungen umfassen Tests, Ruff, Hassfest und HACS. Versionsnummer
und Release-Tag müssen übereinstimmen. Das HACS-Archiv heißt `nuki_direkt.zip`.

Änderungen an der festgelegten pyNukiBT-Version erfordern eine erneute Prüfung
der Verbindungslogik. Fehler bitte in den
[Issues dieses Projekts](https://github.com/gammarider/nuki-direkt/issues) melden.
Keine Geräteadressen, Kopplungsschlüssel, PINs oder HA-Backups veröffentlichen.

## Herkunft und Lizenz

Nuki Direkt basiert auf [ronengr/hass_nuki_bt](https://github.com/ronengr/hass_nuki_bt)
0.0.20 und wird unabhängig von diesem Projekt weiterentwickelt. Übernommene
Urheberhinweise und die [MIT-Lizenz](LICENSE) bleiben erhalten; die Lizenz liegt
auch im installierbaren Komponentenarchiv. Es handelt sich um eine
Weiterentwicklung vorhandenen Codes, nicht um eine vollständige Neuentwicklung.

Das ursprüngliche Projekt basiert auf
[RaspiNukiBridge](https://github.com/regevbr/RaspiNukiBridge) von
[dauden1184](https://github.com/dauden1184/) und [regevbr](https://github.com/regevbr)
und nennt [hass_nuki_ng](https://github.com/kvj/hass_nuki_ng) sowie
[nuki_hub](https://github.com/technyon/nuki_hub) als Inspiration.
Die externe Bibliothek [pyNukiBT](https://pypi.org/project/pyNukiBT/) bleibt eine
separat lizenzierte Abhängigkeit.
