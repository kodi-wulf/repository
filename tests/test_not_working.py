#!/usr/bin/env python3
"""Synthetic offline regression tests: no claim of real Kodi runtime verification."""
import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build import build, prepare_archive_repository, write_browse_indexes
from not_working import load_status, status_for


class NotWorkingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "_data").mkdir()

    def create_package(self, relative="plugins/video/plugin.video.example/plugin.video.example-1.2.3.zip"):
        file = self.root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        xml = b'<addon id="plugin.video.example" name="Example" version="1.2.3" provider-name="Test"><extension point="xbmc.python.pluginsource" library="default.py"><provides>video</provides></extension></addon>'
        with zipfile.ZipFile(file, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("plugin.video.example/addon.xml", xml)
            zf.writestr("plugin.video.example/default.py", "pass\n")
        return file

    def write_status(self, file, status="not working", sha=None):
        record = {
            "addon_id": "plugin.video.example",
            "version": "1.2.3",
            "sha256": sha or hashlib.sha256(file.read_bytes()).hexdigest(),
            "status": status,
            "reason": "Reproducible install failure on test machine",
            "evidence": "tests/evidence/example-log.txt",
            "checked_at": "2026-10-27"
        }
        (self.root / "_data" / "addon_status.json").write_text(
            json.dumps({"schema_version": 1, "packages": [record]}), encoding="utf-8"
        )

    def test_unverified_is_not_assumed_working(self):
        package = self.create_package()
        self.assertEqual(load_status(self.root), {})
        self.assertEqual(status_for(package, "plugin.video.example", "1.2.3", {}), "unverified")

    def test_explicit_evidence_gates_quarantine(self):
        package = self.create_package()
        self.write_status(package)
        decisions = load_status(self.root)
        self.assertEqual(status_for(package, "plugin.video.example", "1.2.3", decisions), "not working")

    def test_hash_change_blocks_old_decision(self):
        package = self.create_package()
        self.write_status(package, sha="0" * 64)
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            status_for(package, "plugin.video.example", "1.2.3", load_status(self.root))

    def test_duplicate_decisions_rejected(self):
        package = self.create_package()
        self.write_status(package)
        path = self.root / "_data" / "addon_status.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["packages"].append(data["packages"][0])
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            load_status(self.root)

    def test_archive_is_opt_in_and_browseable(self):
        package = self.create_package("not-working/plugins/video/plugin.video.example/plugin.video.example-1.2.3.zip")
        archive_info = prepare_archive_repository(self.root, "https://example.invalid/repository/")
        self.assertEqual(archive_info.addon_id, "repository.kodi-wulf.notworking")
        catalog = ET.parse(self.root / "not-working/plugins/video/addons.xml").getroot()
        self.assertEqual([x.attrib["id"] for x in catalog], ["plugin.video.example"])
        path = self.root / "repository/repository.kodi-wulf.notworking/repository.kodi-wulf.notworking-0.1.0.zip"
        with zipfile.ZipFile(path) as zf:
            repo_xml = ET.fromstring(zf.read("repository.kodi-wulf.notworking/addon.xml"))
        dirs = repo_xml.find("./extension[@point='xbmc.addon.repository']")
        self.assertIn("not-working/plugins/video/addons.xml", ET.tostring(dirs, encoding="unicode"))
        self.assertTrue(package.exists())
        write_browse_indexes(self.root)
        self.assertTrue((self.root / "not-working/index.html").exists())
        self.assertTrue((self.root / "not-working/plugins/video/plugin.video.example/index.html").exists())

    def test_reviewed_failure_moves_without_mutation_and_leaves_active_catalog(self):
        source = self.create_package("zips/plugin.video.example-1.2.3.zip")
        original_bytes = source.read_bytes()
        self.write_status(source)
        build(self.root, "https://example.invalid/repository/", "1.33.7a",
              True, self.root.parent / "backup")
        archived = self.root / "not-working/plugins/video/plugin.video.example/plugin.video.example-1.2.3.zip"
        self.assertEqual(archived.read_bytes(), original_bytes)
        active_ids = {node.get("id") for node in ET.parse(self.root / "addons.xml").getroot()}
        archived_ids = {node.get("id") for node in ET.parse(self.root / "not-working/plugins/video/addons.xml").getroot()}
        self.assertNotIn("plugin.video.example", active_ids)
        self.assertIn("repository.kodi-wulf.notworking", active_ids)
        self.assertIn("plugin.video.example", archived_ids)
        self.assertTrue((self.root / "not-working/index.html").is_file())

    def test_empty_archive_still_has_valid_kodi_metadata(self):
        prepare_archive_repository(self.root, "https://example.invalid/repository/")
        self.assertEqual(list(ET.parse(self.root / "not-working/addons.xml").getroot()), [])


if __name__ == "__main__":
    unittest.main()
