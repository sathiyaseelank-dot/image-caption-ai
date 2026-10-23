#!/usr/bin/env python3
"""
Automated Unit Regression Test Suite for Orca Smart Search
----------------------------------------------------------
Tests core logic using mocked Orca CLI responses:
- Snapshot parsing & validation
- DOM pruning
- Semantic scoring & synonym matching
- Target resolution & ambiguity detection
- Page-state comparison (NAVIGATED, VALIDATION_ERROR, MODAL, CONTENT)
- CLI error handling (missing binary, timeout, malformed JSON)
"""

import unittest
from unittest.mock import patch, MagicMock
import json
import subprocess

import orca_smart_search as oss

class TestSnapshotAndPruning(unittest.TestCase):
    def setUp(self):
        self.sample_snapshot = {
            "id": "test-123",
            "ok": True,
            "result": {
                "origin": "https://example.com/checkout",
                "refs": {
                    "e1": {"name": "Checkout Page", "role": "heading"},
                    "e2": {"name": "", "role": "generic"},
                    "e3": {"name": "Full Name", "role": "textbox"},
                    "e4": {"name": "Email Address", "role": "textbox"},
                    "e5": {"name": "Submit Order", "role": "button"},
                    "e6": {"name": "Save Draft", "role": "button"},
                    "e7": {"name": "Save Final", "role": "button"},
                    "e8": {"name": "", "role": "none"}
                }
            }
        }

    def test_dom_pruning_filters_empty_generics(self):
        pruned = oss.smart_dom_prune(self.sample_snapshot)
        ref_ids = [el["ref"] for el in pruned]
        self.assertNotIn("e2", ref_ids, "Empty generic node should be pruned")
        self.assertNotIn("e8", ref_ids, "Empty none role should be pruned")
        self.assertIn("e3", ref_ids)
        self.assertIn("e5", ref_ids)

    def test_dom_pruning_categories(self):
        pruned = oss.smart_dom_prune(self.sample_snapshot)
        by_ref = {el["ref"]: el for el in pruned}
        self.assertTrue(by_ref["e3"]["is_input"])
        self.assertEqual(by_ref["e3"]["suggested_action"], "fill")
        self.assertTrue(by_ref["e5"]["is_button"])
        self.assertEqual(by_ref["e5"]["suggested_action"], "click")

class TestSemanticScoring(unittest.TestCase):
    def setUp(self):
        self.name_element = {"name": "Full Name", "role": "textbox", "semantic_text": "Full Name textbox", "is_interactive": True}
        self.phone_element = {"name": "Telephone Number", "role": "textbox", "semantic_text": "Telephone Number textbox", "is_interactive": True}
        self.submit_btn = {"name": "Submit Order", "role": "button", "semantic_text": "Submit Order button", "is_interactive": True}

    def test_exact_match(self):
        score = oss.compute_semantic_score("Full Name", self.name_element)
        self.assertGreater(score, 5.0)

    def test_case_insensitivity(self):
        score_lower = oss.compute_semantic_score("full name", self.name_element)
        score_upper = oss.compute_semantic_score("FULL NAME", self.name_element)
        self.assertEqual(score_lower, score_upper)
        self.assertGreater(score_lower, 5.0)

    def test_synonym_expansion(self):
        score = oss.compute_semantic_score("phone", self.phone_element)
        self.assertGreater(score, 3.0, "Synonym 'phone' should match 'Telephone Number'")

    def test_generic_role_only_does_not_match_substantive_query(self):
        # Query has substantive word 'nonexistent' plus generic word 'button'
        # Submit Order should NOT match because 'nonexistent' is unmatched
        score = oss.compute_semantic_score("nonexistent button", self.submit_btn)
        self.assertEqual(score, 0.0, "Substantive query with unmatched descriptor must score 0.0")

    def test_whitespace_handling(self):
        score1 = oss.compute_semantic_score("Full Name", self.name_element)
        score2 = oss.compute_semantic_score("   Full    Name   ", self.name_element)
        self.assertGreater(score2, 5.0)

class TestTargetResolutionAndAmbiguity(unittest.TestCase):
    def setUp(self):
        self.snapshot_data = {
            "result": {
                "origin": "https://example.com",
                "refs": {
                    "e1": {"name": "Save Draft", "role": "button"},
                    "e2": {"name": "Save Final", "role": "button"},
                    "e3": {"name": "Customer Name", "role": "textbox"}
                }
            }
        }

    @patch("orca_smart_search.get_orca_snapshot")
    def test_direct_ref_resolution(self, mock_snapshot):
        mock_snapshot.return_value = self.snapshot_data
        ref, el = oss.resolve_target_element("e3")
        self.assertEqual(ref, "e3")
        self.assertEqual(el["name"], "Customer Name")

    @patch("orca_smart_search.get_orca_snapshot")
    def test_ambiguity_raises_error(self, mock_snapshot):
        mock_snapshot.return_value = self.snapshot_data
        with self.assertRaises(oss.AmbiguousTargetError):
            oss.resolve_target_element("Save")

    @patch("orca_smart_search.get_orca_snapshot")
    def test_disambiguated_target_succeeds(self, mock_snapshot):
        mock_snapshot.return_value = self.snapshot_data
        ref, el = oss.resolve_target_element("Save Draft")
        self.assertEqual(ref, "e1")
        self.assertEqual(el["name"], "Save Draft")

    @patch("orca_smart_search.get_orca_snapshot")
    def test_nonexistent_target_raises_error(self, mock_snapshot):
        mock_snapshot.return_value = self.snapshot_data
        with self.assertRaises(oss.ElementNotFoundError):
            oss.resolve_target_element("Credit Card")

class TestPageStateComparison(unittest.TestCase):
    def test_navigation_detected(self):
        before = {"url": "https://example.com/form", "bodyText": "Form"}
        after = {"url": "https://example.com/success", "bodyText": "Success"}
        res = oss.compare_page_states(before, after)
        self.assertEqual(res["verdict"], "NAVIGATED")
        self.assertTrue(res["url_changed"])

    def test_validation_error_on_new_invalid(self):
        before = {"url": "https://example.com/form", "invalidInputs": []}
        after = {
            "url": "https://example.com/form",
            "invalidInputs": [{"name": "email", "id": "email", "label": "Email", "validationMessage": "Required"}]
        }
        res = oss.compare_page_states(before, after)
        self.assertEqual(res["verdict"], "VALIDATION_ERROR")
        self.assertEqual(len(res["new_invalid_inputs"]), 1)

    def test_validation_error_on_new_error_alert(self):
        before = {"url": "https://example.com/form", "alerts": []}
        after = {"url": "https://example.com/form", "alerts": ["Error: Invalid date chosen"]}
        res = oss.compare_page_states(before, after)
        self.assertEqual(res["verdict"], "VALIDATION_ERROR")
        self.assertEqual(res["new_error_alerts"], ["Error: Invalid date chosen"])

    def test_success_notification_on_success_alert(self):
        before = {"url": "https://example.com/form", "alerts": []}
        after = {"url": "https://example.com/form", "alerts": ["Success: Reservation confirmed for Agent Smith!"]}
        res = oss.compare_page_states(before, after)
        self.assertEqual(res["verdict"], "SUCCESS_NOTIFICATION")
        self.assertEqual(res["new_success_alerts"], ["Success: Reservation confirmed for Agent Smith!"])

    def test_parse_date_value_formats(self):
        self.assertEqual(oss.parse_date_value("2002-11-03"), "2002-11-03")
        self.assertEqual(oss.parse_date_value("2002 and nov 03"), "2002-11-03")
        self.assertEqual(oss.parse_date_value("November 3, 2002"), "2002-11-03")
        self.assertEqual(oss.parse_date_value("03/11/2002"), "2002-11-03")
        self.assertIsNone(oss.parse_date_value("just plain text"))

    def test_modal_opened_detected(self):
        before = {"url": "https://example.com/form", "dialogs": []}
        after = {"url": "https://example.com/form", "dialogs": ["Confirmation"]}
        res = oss.compare_page_states(before, after)
        self.assertEqual(res["verdict"], "MODAL_OPENED")
        self.assertTrue(res["dialog_opened"])

    def test_modal_closed_detected(self):
        before = {"url": "https://example.com/form", "dialogs": ["Confirmation"]}
        after = {"url": "https://example.com/form", "dialogs": []}
        res = oss.compare_page_states(before, after)
        self.assertEqual(res["verdict"], "MODAL_CLOSED")
        self.assertTrue(res["dialog_closed"])

    def test_content_updated_detected(self):
        before = {"url": "https://example.com/form", "bodyText": "Counter: 0", "dialogs": [], "invalidInputs": []}
        after = {"url": "https://example.com/form", "bodyText": "Counter: 1", "dialogs": [], "invalidInputs": []}
        res = oss.compare_page_states(before, after)
        self.assertEqual(res["verdict"], "CONTENT_UPDATED")
        self.assertTrue(res["content_changed"])

    def test_no_visible_change(self):
        before = {"url": "https://example.com/form", "bodyText": "Same text", "dialogs": [], "invalidInputs": []}
        after = {"url": "https://example.com/form", "bodyText": "Same text", "dialogs": [], "invalidInputs": []}
        res = oss.compare_page_states(before, after)
        self.assertEqual(res["verdict"], "NO_VISIBLE_CHANGE")

    def test_inspection_failure_does_not_report_clean(self):
        before = {"url": "https://example.com/form"}
        after = {"error": "Browser connection lost", "url": "unknown"}
        res = oss.compare_page_states(before, after)
        self.assertEqual(res["verdict"], "INSPECTION_FAILED")
        self.assertEqual(res["error"], "Browser connection lost")

class TestCliErrorHandling(unittest.TestCase):
    @patch("os.path.exists", return_value=False)
    def test_missing_binary_raises_orca_not_found(self, mock_exists):
        with self.assertRaises(oss.OrcaNotFoundError):
            oss.run_cmd(["/nonexistent/orca", "status"])

    @patch("subprocess.run")
    @patch("os.path.exists", return_value=True)
    def test_process_nonzero_exit_raises_orca_error(self, mock_exists, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "Browser closed"
        mock_run.return_value = mock_proc
        with self.assertRaises(oss.OrcaError) as ctx:
            oss.run_cmd(["orca", "click", "--element", "e99"])
        self.assertIn("Browser closed", str(ctx.exception))

    @patch("subprocess.run")
    @patch("os.path.exists", return_value=True)
    def test_timeout_raises_orca_error(self, mock_exists, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["orca", "snapshot"], timeout=5.0)
        with self.assertRaises(oss.OrcaError) as ctx:
            oss.run_cmd(["orca", "snapshot"], timeout=5.0)
        self.assertIn("timed out", str(ctx.exception))

class TestNewFeatures(unittest.TestCase):
    def test_prune_recognizes_checkboxes_and_radios(self):
        snapshot_data = {
            "result": {
                "origin": "https://example.com",
                "refs": {
                    "e1": {"name": "Accept Terms", "role": "checkbox"},
                    "e2": {"name": "Male", "role": "radio"},
                    "e3": {"name": "Dark Mode", "role": "switch"}
                },
                "snapshot": '- checkbox "Accept Terms" [ref=e1]\n- radio "Male" [ref=e2]\n- switch "Dark Mode" [ref=e3]'
            }
        }
        pruned = oss.smart_dom_prune(snapshot_data)
        self.assertEqual(len(pruned), 3)
        for p in pruned:
            self.assertTrue(p["is_checkable"])
            self.assertTrue(p["is_interactive"])
            self.assertEqual(p["suggested_action"], "check")

    def test_act_checkbox_radio_invalid_action(self):
        with self.assertRaises(ValueError):
            oss.act_checkbox_radio("e1", action="invalid_action")

    def test_fill_form_invalid_json_raises(self):
        with self.assertRaises(oss.OrcaError) as ctx:
            oss.act_fill_form("this is not json")
        self.assertIn("Invalid JSON", str(ctx.exception))

    def test_fill_form_non_dict_json_raises(self):
        with self.assertRaises(oss.OrcaError) as ctx:
            oss.act_fill_form('["item1", "item2"]')
        self.assertIn("JSON object", str(ctx.exception))

    @patch("orca_smart_search.act_fill_and_verify")
    @patch("orca_smart_search.act_checkbox_radio")
    @patch("orca_smart_search.get_page_verification_state", return_value={"invalidInputs": []})
    def test_fill_form_processes_text_and_booleans(self, mock_state, mock_check, mock_fill):
        payload = json.dumps({
            "Full Name": "Test User",
            "I agree": True,
            "Opt out": False
        })
        res = oss.act_fill_form(payload)
        self.assertEqual(res["total"], 3)
        self.assertEqual(res["success_count"], 3)
        mock_fill.assert_called_once_with("Full Name", "Test User")
        self.assertEqual(mock_check.call_count, 2)
        mock_check.assert_any_call("I agree", "check")
        mock_check.assert_any_call("Opt out", "uncheck")

    def test_option_unavailable_error(self):
        err = oss.OptionUnavailableError("Option '2001' not in [2026, 2027]")
        self.assertIsInstance(err, oss.OrcaError)

    @patch("orca_smart_search.resolve_target_element")
    def test_click_disabled_element_raises_element_disabled_error(self, mock_resolve):
        mock_resolve.return_value = ("e100", {"name": "Previous year", "role": "button", "disabled": True})
        with self.assertRaises(oss.ElementDisabledError) as ctx:
            oss.act_click_and_verify("Previous year")
        self.assertIn("disabled", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()
