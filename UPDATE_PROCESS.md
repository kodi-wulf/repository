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

## Verified Not Working archive

The status decisions live in `_data/addon_status.json` and must refer to the exact ZIP SHA-256, add-on ID, version, dated reproduction and test evidence. Old, archived or offline upstream repositories are **not** automatically declared broken. Do not populate status decisions based on the existing `DEAD` audit flag alone.

The repository builder preserves SHA-verified failures under `not-working/` and keeps them out of the active `addons.xml`. A separate Kodi archive repository is generated for optional installation and displayed under **Add-on repositories**. The WWW ZIP browser has a first-level `not-working/` folder.

Before publishing new classifications, run `python3 -m unittest discover -s tests -p 'test_not_working.py' -v` and the normal repository checks, followed by a manual Kodi installation test on the intended Kodi version. See [the complete policy](docs/NOT_WORKING_POLICY.md).

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
