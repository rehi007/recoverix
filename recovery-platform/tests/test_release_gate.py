"""Release readiness 및 final_release_gate 일관성·FAIL 조건 (stdlib unittest, mock)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tools.release_gate_lib as rgl
from tools.release_gate_lib import Signal, compute_readiness_tier
from tools import final_release_gate as frg
from tools import release_readiness_check as rrc


def _minimal_signals() -> list[Signal]:
    """build_readiness_payload 축약 mock용."""
    return [
        Signal("required_scripts", "PASS", ""),
        Signal("required_docs", "PASS", ""),
        Signal("product_version", "PASS", ""),
        Signal("efi_source_assets", "PASS", ""),
        Signal("runtime_modules", "PASS", ""),
        Signal("placeholder_scan", "PASS", ""),
        Signal("safety_audit", "PASS", "PASS", {}),
        Signal("destructive_command_audit", "PASS", "", {"complete": True, "status": "PASS"}),
        Signal("integration_checks", "PASS", "PASS", {}),
        Signal("validate_release", "PASS", "", {}),
        Signal("pre_destructive_gate", "PASS", "PASS", {}),
    ]


def _payload_from_signals(signals: list[Signal]) -> dict:
    readiness, stage = compute_readiness_tier(signals)
    dest = next((s for s in signals if s.id == "destructive_command_audit"), None)
    dest_complete = bool((dest.data or {}).get("complete")) if dest else False
    pd = next((s for s in signals if s.id == "pre_destructive_gate"), None)
    pd_overall = pd.detail if pd else "UNKNOWN"
    return {
        "generated_at": "fixed",
        "readiness": readiness,
        "status": stage if readiness != "NOT_READY" else "NOT_READY",
        "release_stage_recommendation": stage,
        "safety_audit": next((s.detail for s in signals if s.id == "safety_audit"), "UNKNOWN"),
        "integration_checks": next((s.detail for s in signals if s.id == "integration_checks"), "UNKNOWN"),
        "pre_destructive_gate": pd_overall,
        "destructive_audit_complete": dest_complete,
        "required_docs": all(s.status == "PASS" for s in signals if s.id == "required_docs"),
        "required_assets": True,
        "known_limitations": [],
        "bundle_path": str(rgl.DEFAULT_BUNDLE),
        "signals": [s.to_dict() for s in signals],
        "pre_destructive_report_summary": {"overall": pd_overall, "steps": 0},
        "safety_report_summary": {},
    }


class ReleaseGateLibUnitTests(unittest.TestCase):
    def test_required_doc_missing_per_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "config").mkdir()
            (root / "docs").mkdir()
            (root / "config" / "product_manifest.json").write_text(
                json.dumps({"required_docs": ["NOT_THERE.md"], "placeholder_tokens": []}),
                encoding="utf-8",
            )
            sig = rgl.signal_manifest_docs(root)
            self.assertEqual(sig.status, "FAIL")
            self.assertIn("missing_docs", sig.data)

    def test_placeholder_token_in_code_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "config").mkdir()
            (root / "common").mkdir(parents=True)
            (root / "config" / "product_manifest.json").write_text(
                json.dumps({"placeholder_tokens": ["PLACEHOLDER_DISTRIBUTION"]}),
                encoding="utf-8",
            )
            (root / "common" / "bad.py").write_text("# x\nPLACEHOLDER_DISTRIBUTION\n", encoding="utf-8")
            sig = rgl.scan_placeholder_tokens(root)
            self.assertEqual(sig.status, "FAIL")

    def test_efi_source_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "boot_manager" / "assets" / "recovery_boot").mkdir(parents=True)
            sig = rgl.signal_efi_source(root)
            self.assertEqual(sig.status, "FAIL")

    def test_validate_release_skipped_when_bundle_missing(self):
        with tempfile.TemporaryDirectory() as td:
            sig = rgl.run_validate_release_bundle(rgl.REPO_ROOT, Path(td) / "no_bundle")
            self.assertEqual(sig.status, "SKIPPED")

    def test_compute_not_ready_when_safety_fail(self):
        sigs = list(_minimal_signals())
        idx = next(i for i, s in enumerate(sigs) if s.id == "safety_audit")
        sigs[idx] = Signal("safety_audit", "FAIL", "FAIL", {})
        self.assertEqual(compute_readiness_tier(sigs)[0], "NOT_READY")

    def test_compute_not_ready_when_destructive_audit_fail(self):
        sigs = list(_minimal_signals())
        idx = next(i for i, s in enumerate(sigs) if s.id == "destructive_command_audit")
        sigs[idx] = Signal(
            "destructive_command_audit",
            "FAIL",
            "incomplete",
            {"complete": False, "status": "FAIL"},
        )
        self.assertEqual(compute_readiness_tier(sigs)[0], "NOT_READY")

    def test_validate_release_fail_is_not_ready(self):
        sigs = list(_minimal_signals())
        idx = next(i for i, s in enumerate(sigs) if s.id == "validate_release")
        sigs[idx] = Signal("validate_release", "FAIL", "bad bundle", {})
        self.assertEqual(compute_readiness_tier(sigs)[0], "NOT_READY")

    def test_readiness_payload_json_serializable(self):
        sample = _payload_from_signals(_minimal_signals())
        dec = json.loads(json.dumps(sample, ensure_ascii=False))
        self.assertEqual(dec["readiness"], "READY")


class ReleaseGateIntegrationMockTests(unittest.TestCase):
    @patch("tools.release_gate_lib.run_pre_destructive_gate", return_value=("PASS", {}))
    @patch("tools.release_gate_lib.run_validate_release_bundle")
    @patch("tools.release_gate_lib.run_integration")
    @patch("tools.release_gate_lib.run_safety")
    def test_readiness_pass_all_mocked_ready_or_warn_only(
        self,
        mock_safety,
        mock_integration,
        mock_validate,
        mock_pre,
    ):
        mock_safety.return_value = (
            Signal("safety_audit", "PASS", "PASS_WITH_WARNINGS", {}),
            {"destructive_command_audit": {"complete": True, "status": "PASS"}},
        )
        mock_integration.return_value = Signal("integration_checks", "PASS", "PASS", {})
        mock_validate.return_value = Signal("validate_release", "PASS", "ok", {})
        dummy_bundle = Path(tempfile.mkdtemp()) / "nope"
        payload = rgl.build_readiness_payload(rgl.REPO_ROOT, bundle=dummy_bundle)
        self.assertIn(payload["readiness"], ("READY", "READY_WITH_WARNINGS"))
        mock_pre.assert_called_once()

    @patch("tools.release_gate_lib.build_readiness_payload")
    def test_final_gate_fails_when_readiness_not_ready(self, mock_build):
        failing = Signal("required_docs", "FAIL", "", {"missing_docs": ["x"]})
        signals = [failing] + [s for s in _minimal_signals() if s.id != "required_docs"]
        mock_build.return_value = _payload_from_signals(signals)
        out = rgl.final_gate_payload(rgl.REPO_ROOT)
        self.assertEqual(out["overall"], "FAIL")
        self.assertFalse(out["destructive_test_allowed"])

    @patch("tools.release_gate_lib.build_readiness_payload")
    def test_final_gate_fails_when_pre_destructive_fail(self, mock_build):
        sigs = list(_minimal_signals())
        idx = next(i for i, s in enumerate(sigs) if s.id == "pre_destructive_gate")
        sigs[idx] = Signal("pre_destructive_gate", "FAIL", "FAIL", {})
        mock_build.return_value = _payload_from_signals(sigs)
        out = rgl.final_gate_payload(rgl.REPO_ROOT)
        self.assertEqual(out["overall"], "FAIL")

    @patch("tools.release_gate_lib.build_readiness_payload")
    def test_final_gate_pass_matches_readiness_pass_and_validate(self, mock_build):
        mock_build.return_value = _payload_from_signals(_minimal_signals())
        out = rgl.final_gate_payload(rgl.REPO_ROOT)
        self.assertEqual(out["overall"], "PASS")
        self.assertTrue(out["destructive_test_allowed"])
        self.assertEqual(out["readiness"], "READY")


class ReleaseReadinessCliTests(unittest.TestCase):
    def test_main_writes_file(self):
        sample = _payload_from_signals(_minimal_signals())
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "rel.json"
            with patch.object(rrc, "OUT_PATH", out):
                with patch.object(rrc, "build_readiness_payload", return_value=sample):
                    exit_code = rrc.main([])
            self.assertTrue(out.is_file())
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["readiness"], sample["readiness"])
            self.assertEqual(exit_code, 0)

    def test_final_gate_cli_writes_mock_pass(self):
        merged = {
            "generated_at": "t",
            "overall": "PASS",
            "destructive_test_allowed": True,
            "message": "ok",
            "reasons": [],
            "readiness": "READY",
            "release_stage_recommendation": "READY_FOR_MANUAL_TEST",
            "pre_destructive_gate": "PASS",
            "validate_release_signal": {"id": "validate_release", "status": "PASS"},
            "known_limitations": [],
            "signals": [],
        }
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "final.json"
            with patch.object(frg, "OUT_PATH", out):
                with patch.object(frg, "final_gate_payload", return_value=merged):
                    exit_code = frg.main([])
            self.assertTrue(out.is_file())
            self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
