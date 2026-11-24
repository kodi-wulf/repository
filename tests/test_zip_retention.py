import contextlib
import io
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest
from urllib.parse import quote
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from build import DEFAULT_BASE_URL, DEFAULT_REPO_VERSION, REPO_ID, build, installer_name


def package(path, addon_id, *, member_root=None, document=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if document is None:
        document = f'<addon id="{addon_id}" version="1.0"><extension point="xbmc.addon.metadata"><assets><icon>icon.png</icon></assets></extension></addon>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{member_root or addon_id}/addon.xml", document)
        archive.writestr(f"{member_root or addon_id}/icon.png", b"icon")
    return path.read_bytes()


class ZipRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_build(self, **options):
        with contextlib.redirect_stdout(io.StringIO()):
            build(self.root, DEFAULT_BASE_URL, DEFAULT_REPO_VERSION, backup=None, **options)

    def test_defective_zip_downloads_are_separate_from_installation(self):
        valid_id = "repository.valid"
        valid = self.root / "repository" / valid_id / f"{valid_id}-1.0.zip"
        original_valid = package(valid, valid_id)
        bad_xml = self.root / "repository" / "repository.bad" / "broken; manifest.zip"
        original_bad_xml = package(bad_xml, "repository.bad", document="<addon>")
        corrupt = self.root / "zips" / "_unreadable.ZIP"
        corrupt.parent.mkdir()
        original_corrupt = b"not a ZIP"
        corrupt.write_bytes(original_corrupt)
        reserved = self.root / "defekt" / "zips" / "file-_unreadable.ZIP"
        reserved.parent.mkdir(parents=True)
        reserved.write_bytes(b"prior upload with the same name")

        self.run_build(apply=True)

        self.assertEqual(valid.read_bytes(), original_valid)
        self.assertEqual((self.root / "defekt" / bad_xml.relative_to(self.root)).read_bytes(), original_bad_xml)
        self.assertFalse(bad_xml.exists())
        self.assertEqual(reserved.read_bytes(), b"prior upload with the same name")
        self.assertEqual(reserved.with_name("file-_unreadable-1.ZIP").read_bytes(), original_corrupt)
        for catalog in (self.root / "addons.xml", self.root / "repository" / "addons.xml"):
            self.assertEqual([addon.get("id") for addon in ET.parse(catalog).getroot()], [valid_id])
        with zipfile.ZipFile(self.root / installer_name()) as installer:
            metadata = ET.fromstring(installer.read(f"{REPO_ID}/addon.xml"))
            self.assertEqual([entry.text for entry in metadata.findall(".//datadir")], [DEFAULT_BASE_URL + "repository/"])
        addon_index = (valid.parent / "index.html").read_text()
        self.assertIn(f'href="{valid.name}"', addon_index)
        root_index = (self.root / "index.html").read_text()
        static_nav = re.search(r'<nav class="kodi-static"[^>]*>(.*?)</nav>', root_index, re.S).group(1)
        self.assertNotIn("defekt", static_nav)
        section = (self.root / "defekt" / "index.html").read_text()
        for zip_path in (path for path in (self.root / "defekt").rglob("*") if path.suffix.casefold() == ".zip"):
            link = quote(zip_path.relative_to(self.root / "defekt").as_posix(), safe="/._-~")
            self.assertIn(f'href="{link}" download=""', section)
        browser = (self.root / "assets" / "files.js").read_text()
        data, _ = json.JSONDecoder().raw_decode(browser.removeprefix("window.KODIWULF_FILES="))
        self.assertEqual(sum(file["category"] == "defekt" for file in data), 3)

        paths_before = sorted(self.root.rglob("*.zip"))
        bytes_before = [path.read_bytes() for path in paths_before]
        self.run_build(apply=True)
        self.assertEqual(sorted(self.root.rglob("*.zip")), paths_before)
        self.assertEqual([path.read_bytes() for path in paths_before], bytes_before)

    def test_old_and_repaired_zip_bytes_remain_downloadable(self):
        active = self.root / "repository" / "repository.valid" / "repository.valid-1.0.zip"
        previous = package(active, "repository.valid")
        replacement = self.root / "zips" / "original.zip"
        original = package(replacement, "repository.valid", member_root="different-root")
        mirror = self.root / "repository" / "plugins" / "old.zip"
        archive_only = package(mirror, "repository.old")
        older_installer = self.root / f"{REPO_ID}-v0.1.zip"
        older = package(older_installer, REPO_ID)

        self.run_build(apply=True, cleanup_legacy=True)

        saved = [path.read_bytes() for path in (self.root / "archiv").rglob("*.zip")]
        self.assertIn(previous, saved)
        self.assertIn(original, saved)
        self.assertIn(older, saved)
        with zipfile.ZipFile(active) as archive:
            self.assertIn("repository.valid/addon.xml", archive.namelist())
        old_active = self.root / "repository" / "repository.old" / "repository.old-1.0.zip"
        self.assertEqual(old_active.read_bytes(), archive_only)
        self.assertFalse(mirror.parent.exists())
        self.assertFalse(replacement.exists())
        self.assertIn('download=""', (self.root / "archiv" / "index.html").read_text())

    def test_installer_only_does_not_keep_a_stale_defective_entry(self):
        valid = self.root / "repository" / "repository.valid" / "repository.valid-1.0.zip"
        bad = self.root / "repository" / "repository.bad" / "repository.bad-1.0.zip"
        package(valid, "repository.valid")
        package(bad, "repository.bad")
        self.run_build(apply=True)
        bad.write_bytes(b"damaged after initial build")

        self.run_build(apply=False, installer_only=True)
        self.run_build(apply=False, site_only=True)

        defect = self.root / "defekt" / bad.relative_to(self.root)
        self.assertEqual(defect.read_bytes(), b"damaged after initial build")
        self.assertNotIn("repository.bad", (self.root / "addons.xml").read_text())
        self.assertNotIn("repository.bad", (self.root / "repository" / "addons.xml").read_text())
        self.assertIn(defect.name, (self.root / "defekt" / "index.html").read_text())

    def test_unsupported_compression_is_preserved_as_defective(self):
        source = self.root / "zips" / "unsupported.zip"
        original = bytearray(package(source, "repository.unsupported"))
        first_central_entry = original.index(b"PK\x01\x02")
        struct.pack_into("<H", original, first_central_entry + 10, 123)
        source.write_bytes(original)

        self.run_build(apply=True)

        saved = self.root / "defekt" / "zips" / source.name
        self.assertEqual(saved.read_bytes(), original)

    def test_symbolic_link_aborts_before_any_package_mutation(self):
        with tempfile.TemporaryDirectory() as external:
            destination = Path(external) / "outside.zip"
            original = package(destination, "repository.outside")
            link = self.root / "exposed.zip"
            link.symlink_to(destination)

            with self.assertRaisesRegex(RuntimeError, "symbolic link cannot be published"):
                self.run_build(apply=True)

            self.assertTrue(link.is_symlink())
            self.assertEqual(destination.read_bytes(), original)
            self.assertFalse((self.root / "addons.xml").exists())


if __name__ == "__main__":
    unittest.main()
