#!/usr/bin/env python3
"""Offline Omega metadata fixture tests. Neither source nor Kodi runtime is contacted."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from discover_omega import compare, plugin_inventory, version_key


class DiscoveryTests(unittest.TestCase):
    def test_version_order_is_numeric(self):
        self.assertGreater(version_key("10.0"), version_key("2.1"))

    def test_gap_is_discovery_not_working_claim(self):
        official = b"""<addons>
        <addon id="plugin.video.existing" name="Existing" version="2.0"><extension point="xbmc.python.pluginsource"/></addon>
        <addon id="plugin.audio.new" name="New" version="3.0"><extension point="xbmc.python.pluginsource"/></addon>
        <addon id="script.module.misc" name="Module" version="1.0"><extension point="xbmc.python.module"/></addon>
        </addons>"""
        local = b"""<addons><addon id="plugin.video.existing" name="Existing" version="1.0"/></addons>"""
        report = compare(official, local, "https://example.invalid/addons.xml")
        self.assertEqual(report["official_plugin_count"], 2)
        self.assertEqual(report["present_in_kodiwulf"], 1)
        self.assertEqual(report["missing_from_kodiwulf"], 1)
        self.assertEqual(report["candidates"][0]["addon_id"], "plugin.audio.new")
        self.assertIn("unverified", report["verification"])


if __name__ == "__main__":
    unittest.main()
