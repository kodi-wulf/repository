# # # # # # # # # # # # # # #
# Kodi-Wulf Update Process
# # # # # # # # # # # # # # #

## 1. Research original repository installers

Für externe Kodi-Repositories gilt: keine fremden Installer erzeugen oder aus einer gefundenen `addon.xml` rekonstruieren.

Gesucht wird der vom jeweiligen Upstream tatsächlich veröffentlichte Repository-Installer (`*.zip`). Eine `addon.xml`, ein GitHub-Repository oder eine Repository-URL allein reicht nicht als importierbares Paket.

Nur echte Kodi-Repository-Add-ons mit `extension point="xbmc.addon.repository"` gehören in diesen Bestand. Direkte Plugins, Scripts, Services und Module werden nicht importiert.

## 2. Place original source ZIPs locally

Der kanonische Intake-/Staging-Ordner der bestehenden Tools ist:

    zips/

Gefundene originale Repository-Installer werden dort abgelegt, damit die Python-Buildtools sie erfassen. Kein paralleler Ersatzordner wie `xyz/` oder `incoming/` soll für diesen Workflow eingeführt werden.

`zips/` ist ein temporärer lokaler Build-Eingang und wird nicht committed. Der Ordner darf fehlen, solange er leer ist.

## 3. Rebuild

    python tools/build.py --apply

`tools/build.py` übernimmt danach Validierung, Deduplizierung, Klassifizierung, Überführung in die öffentliche Repository-Struktur sowie die Website-/Kodi-Metadaten.

Die von diesem Projekt selbst erzeugte `repository.kodi-wulf-v<version>.zip` bleibt ein zulässiges Kodi-Wulf-Buildartefakt. Diese Ausnahme gilt nicht für fremde Repository-Installer.

## 4. Validate

Run:

    python -m py_compile tools/build.py tools/kodiwulf_dark_index.py tools/validate_repo.py
    python tools/validate_repo.py

Expected repository metadata:

    repository.kodi-wulf 1.33.7a: present
    addons.xml.md5: matches addons.xml

## 5. Commit and push

Nur die durch den Build erzeugte veröffentlichte Struktur und die dazugehörigen Metadaten committen. Den temporären `zips/`-Intake nicht committen.

    git add -A -- .
    git commit -m "feat(repo): update Kodi-Wulf packages"
    git push origin main

## 6. Online checks

    curl -L -I "https://kodi-wulf.github.io/repository/"
    curl -L -I "https://kodi-wulf.github.io/repository/addons.xml"
    curl -L -I "https://kodi-wulf.github.io/repository/addons.xml.md5"
    curl -L -I "https://kodi-wulf.github.io/repository/repository.kodi-wulf-v1.33.7a.zip"
