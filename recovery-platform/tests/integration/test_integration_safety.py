"""Integration safety tests (dry-run tooling; no destructive apply)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "tools" / "run_integration_checks.py"
COLLECT = REPO / "tools" / "collect_diagnostics.py"


def _env() -> dict[str, str]:
    e = os.environ.copy()
    e["PYTHONPATH"] = str(REPO)
    return e


class TestIntegrationSafety(unittest.TestCase):
    def test_runner_requires_dry_run(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(RUNNER)],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            env=_env(),
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0, "missing --dry-run should fail")

    def test_runner_rejects_apply_argv(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(RUNNER), "--dry-run", "--apply"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            env=_env(),
            check=False,
        )
        self.assertEqual(proc.returncode, 2)

    def test_runner_source_has_no_apply_argument(self) -> None:
        src = RUNNER.read_text(encoding="utf-8")
        self.assertNotIn('add_argument("--apply"', src)

    def test_runner_writes_report_with_check_statuses(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(RUNNER), "--dry-run"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            env=_env(),
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        out_path = REPO / "diagnostics" / "integration_report.json"
        self.assertTrue(out_path.is_file(), "report must be created")
        data = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertIn("checks", data)
        self.assertIn("status", data)
        statuses = {c["status"] for c in data["checks"]}
        self.assertTrue(statuses.issubset({"PASS", "FAIL", "SKIPPED"}), statuses)

    def test_restore_planner_dry_run_execution_disabled(self) -> None:
        if sys.platform != "linux":
            self.skipTest("restore planner integration on Linux recovery host")
        from restore_engine.restore_planner import create_restore_plan

        plan = create_restore_plan(live=False)
        self.assertFalse(plan.execution_allowed)

    def test_report_restore_planner_never_allows_execution(self) -> None:
        subprocess.run(
            [sys.executable, str(RUNNER), "--dry-run"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            env=_env(),
            check=False,
        )
        data = json.loads((REPO / "diagnostics" / "integration_report.json").read_text(encoding="utf-8"))
        restore = next(c for c in data["checks"] if c["name"] == "restore_planner_dry_run")
        if restore["status"] == "SKIPPED":
            self.skipTest("restore planner skipped off-Linux")
        self.assertFalse(restore["details"].get("execution_allowed", True))

    def test_collect_diagnostics_writes_and_masks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "d.json"
            proc = subprocess.run(
                [sys.executable, str(COLLECT), "--output", str(out)],
                cwd=str(REPO),
                capture_output=True,
                text=True,
                env=_env(),
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(out.is_file())
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn("python", payload)

        spec = importlib.util.spec_from_file_location("collect_diagnostics", COLLECT)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        masked = mod.mask_sensitive(
            {"disk": "{11111111-1111-1111-1111-111111111111}", "x": "n" * 300}
        )
        self.assertIn("****", masked["disk"])
        self.assertTrue(len(masked["x"]) < 300)


if __name__ == "__main__":
    unittest.main()
