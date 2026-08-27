# Kodi-Wulf Repository Agent Directives

## Verbindliche Repository-Only-Quellenregel

Für recherchierte externe Kodi-Repositories gilt verbindlich:

1. Fremde Repository-Installer niemals selbst erzeugen, rekonstruieren oder aus einer `addon.xml` künstlich paketieren.
2. Stattdessen den bereits vom jeweiligen Upstream veröffentlichten originalen Repository-Installer (`*.zip`) recherchieren und beziehen.
3. Gefundene Repository-Installer ausschließlich als Build-Eingang unter `zips/` ablegen.
4. `zips/` ist der kanonische temporäre Intake-/Staging-Ordner des bestehenden Toolchains. Kein neuer Ersatzordner wie `xyz/`, `incoming/` oder eine weitere parallele Importstruktur darf eingeführt werden, solange die Tools nicht ausdrücklich gemeinsam umgestellt werden.
5. Erst `tools/build.py` darf die ZIPs einlesen, `addon.xml` validieren, nach ID/Version deduplizieren, in die öffentliche Repository-Struktur überführen und die Website-/Kodi-Metadaten aktualisieren.
6. In den extern recherchierten Bestand dürfen nur echte Kodi-Repository-Add-ons aufgenommen werden, also Pakete mit `extension point="xbmc.addon.repository"`.
7. Plugins, Scripts, Services, Module oder andere direkte Add-ons dürfen nicht als recherchierte Repository-Installer in den Katalog aufgenommen werden.
8. Die lokal erzeugte Kodi-Wulf-eigene Installer-ZIP `repository.kodi-wulf-v<version>.zip` ist von Regel 1 ausgenommen; sie ist ein Build-Artefakt dieses Projekts. Die Ausnahme gilt nicht für fremde Repositories.

## Recherche

Bei der Repository-Recherche muss der Agent den tatsächlichen Upstream-Download eines Repository-ZIPs finden. Eine gefundene `addon.xml`, ein GitHub-Quellrepository oder eine Repository-URL allein berechtigt nicht dazu, selbst eine Installer-ZIP zu erzeugen.

Wenn kein original veröffentlichter Installer auffindbar oder verifizierbar ist, bleibt der Kandidat als nicht importiert dokumentiert, anstatt künstlich paketiert zu werden.

## Build-Vertrag

Der bestehende Tool-Vertrag ist maßgeblich:

- Intake: `zips/**/*.zip`
- Build: `python tools/build.py --apply`
- veröffentlichte Repository-Pakete: `repository/<addon.id>/...`
- generierte Metadaten/Webnavigation: ausschließlich durch die vorhandenen Build-Tools

Änderungen an dieser Quellenregel bedürfen einer expliziten Benutzerentscheidung.
