#!/usr/bin/env python3
"""
Live Browser Integration Test Suite for Orca Smart Search
----------------------------------------------------------
Executes real actions against the live Orca browser instance
and the test harness page (http://localhost:8765/test_harness_page.html).
"""

import unittest
import subprocess
import json
import time

import orca_smart_search as oss

TEST_HARNESS_URL = "http://localhost:8765/test_harness_page.html"

class TestOrcaLiveBrowserIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Navigate browser to test harness page
        oss.run_cmd([oss.ORCA_BIN, "goto", "--url", TEST_HARNESS_URL])
        time.sleep(1.0)

    def setUp(self):
        # Ensure we are on the base test page before each test
        state = oss.get_page_verification_state()
        if "test_harness_page.html" not in state.get("url", ""):
            oss.run_cmd([oss.ORCA_BIN, "goto", "--url", TEST_HARNESS_URL])
            time.sleep(1.0)

    def test_01_live_snapshot_structure(self):
        snapshot = oss.get_orca_snapshot()
        self.assertIn("result", snapshot)
        self.assertIn("refs", snapshot["result"])
        self.assertIn("origin", snapshot["result"])
        self.assertTrue(len(snapshot["result"]["refs"]) >= 10)

    def test_02_live_dom_pruning_and_categories(self):
        snapshot = oss.get_orca_snapshot()
        pruned = oss.smart_dom_prune(snapshot)
        names = [el["name"] for el in pruned]
        self.assertIn("Full Name", names)
        self.assertIn("Email Address", names)
        self.assertIn("Telephone Number", names)
        self.assertIn("Submit Order", names)

    def test_03_live_fill_and_readback(self):
        fill_res = oss.act_fill_and_verify("Full Name", "Integration Tester")
        self.assertEqual(fill_res["verdict"], "NO_VISIBLE_CHANGE")
        # Read back from live browser
        val = oss.run_cmd([oss.ORCA_BIN, "eval", "--expression", "document.getElementById('fullName').value"])
        self.assertEqual(val, "Integration Tester")

    def test_04_live_ambiguity_handling(self):
        with self.assertRaises(oss.AmbiguousTargetError):
            oss.resolve_target_element("Save")

    def test_05_live_modal_dialog_lifecycle(self):
        # Open dialog
        open_res = oss.act_click_and_verify("Open Dialog", wait_seconds=0.8)
        self.assertEqual(open_res["verdict"], "MODAL_OPENED")
        self.assertIn("Test Dialog Box", open_res["active_dialogs"])

        # Close dialog
        close_res = oss.act_click_and_verify("Close Dialog", wait_seconds=0.8)
        self.assertEqual(close_res["verdict"], "MODAL_CLOSED")

    def test_06_live_content_mutation_without_nav(self):
        res = oss.act_click_and_verify("Increment Counter", wait_seconds=0.5)
        self.assertEqual(res["verdict"], "CONTENT_UPDATED")

    def test_07_live_form_submission_navigation(self):
        oss.act_fill_and_verify("Full Name", "Valid User")
        oss.act_fill_and_verify("Email Address", "valid@domain.com")
        oss.act_fill_and_verify("Telephone Number", "123-456-7890")
        sub_res = oss.act_click_and_verify("Submit Order", wait_seconds=1.2)
        self.assertEqual(sub_res["verdict"], "NAVIGATED")
        self.assertIn("success.html", sub_res["to_url"])

if __name__ == "__main__":
    unittest.main()
