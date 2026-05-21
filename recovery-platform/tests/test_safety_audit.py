"""Tests for safety_audit and related audit tooling (stdlib unittest)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import code_policy_audit as cp
from tools import safety_audit as sa
from tools.destructive_command_audit import classify_file, run_destructive_audit


def _fixture_config(repo_root: Path) -> dict:
    return json.loads((repo_root / "tools" / "forbidden_audit_patterns.json").read_text(encoding="utf-8"))


class SafetyAuditAggregateTests(unittest.TestCase):
    def test_aggregate_fail_on_patterns(self):
        hits = [
            sa.PatternHit("x", "FAIL", "lbl", "a.py"),
        ]
        self.assertEqual(sa._aggregate_status(hits, "PASS", "PASS"), "FAIL")

    def test_pass_with_warnings(self):
        hits = [
            sa.PatternHit("x", "WARN", "lbl", "docs/a.md"),
        ]
        self.assertEqual(sa._aggregate_status(hits, "PASS", "PASS"), "PASS_WITH_WARNINGS")


class SafetyAuditPatternTests(unittest.TestCase):
    def test_disk0_pattern_fail_in_code(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _fixture_config(sa.REPO_ROOT)
            (root / "module.py").write_text('path = "Disk 0"\n', encoding="utf-8")
            hits = sa.scan_text_patterns(root, cfg)
            self.assertTrue(any(h.pattern_id == "disk0_word" and h.severity == "FAIL" for h in hits))

    def test_boot0000_pattern_fail_in_code(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _fixture_config(sa.REPO_ROOT)
            (root / "bad.py").write_text("entry = 'Boot0000'\n", encoding="utf-8")
            hits = sa.scan_text_patterns(root, cfg)
            self.assertTrue(any(h.pattern_id == "boot0000" and h.severity == "FAIL" for h in hits))

    def test_dev_sda_pattern_fail_in_code(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _fixture_config(sa.REPO_ROOT)
            (root / "disk.py").write_text('dev = "/dev/sda"\n', encoding="utf-8")
            hits = sa.scan_text_patterns(root, cfg)
            self.assertTrue(any(h.pattern_id == "dev_sda" and h.severity == "FAIL" for h in hits))

    def test_doc_example_is_warning_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _fixture_config(sa.REPO_ROOT)
            doc = root / "docs"
            doc.mkdir()
            (doc / "note.md").write_text("Do not use Boot0000.\n", encoding="utf-8")
            hits = sa.scan_text_patterns(root, cfg)
            self.assertTrue(hits)
            self.assertTrue(all(h.severity == "WARN" for h in hits))


class DestructiveSinkTests(unittest.TestCase):
    """destructive_command_audit 개별 sink 분류 테스트."""

    def test_unguarded_partclone_run_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            be = root / "backup_engine"
            be.mkdir(parents=True)
            path = be / "unguarded.py"
            path.write_text(
                "from common.command import run_command\n"
                "def x():\n"
                "    return run_command(['partclone.ntfs', '-r'], dry_run=False)\n",
                encoding="utf-8",
            )
            rows = classify_file(root, path)
            self.assertTrue(any(r.category == "DESTRUCTIVE_UNGUARDED" for r in rows))

    def test_unguarded_shutil_rmtree(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            be = root / "backup_engine"
            be.mkdir(parents=True)
            path = be / "rmtree.py"
            path.write_text(
                "import shutil\n"
                "def boom():\n"
                "    shutil.rmtree('/tmp/x')\n",
                encoding="utf-8",
            )
            rows = classify_file(root, path)
            self.assertTrue(
                any(r.sink == "shutil.rmtree" and r.category == "DESTRUCTIVE_UNGUARDED" for r in rows)
            )

    def test_unguarded_path_unlink(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            be = root / "backup_engine"
            be.mkdir(parents=True)
            path = be / "unlink.py"
            path.write_text(
                "from pathlib import Path\n"
                "def boom():\n"
                "    Path('/tmp/x').unlink()\n",
                encoding="utf-8",
            )
            rows = classify_file(root, path)
            self.assertTrue(
                any(
                    r.sink == "pathlib.Path.unlink" and r.category == "DESTRUCTIVE_UNGUARDED" for r in rows
                )
            )

    def test_unguarded_write_text(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            be = root / "backup_engine"
            be.mkdir(parents=True)
            path = be / "wt.py"
            path.write_text(
                "from pathlib import Path\n"
                "def boom():\n"
                "    Path('/tmp/x').write_text('a', encoding='utf-8')\n",
                encoding="utf-8",
            )
            rows = classify_file(root, path)
            self.assertTrue(any(r.sink == "write_text" and r.category == "DESTRUCTIVE_UNGUARDED" for r in rows))

    def test_unguarded_sgdisk_load_backup_run_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            be = root / "backup_engine"
            be.mkdir(parents=True)
            path = be / "sg.py"
            path.write_text(
                "from common.command import run_command\n"
                "def boom():\n"
                "    run_command(['sgdisk', '--load-backup', '/b', '/dev/sdx'], dry_run=False)\n",
                encoding="utf-8",
            )
            rows = classify_file(root, path)
            self.assertTrue(any(r.category == "DESTRUCTIVE_UNGUARDED" for r in rows))
            self.assertTrue(any("sgdisk" in r.sink for r in rows))

    def test_unguarded_bcdedit_set_subprocess(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            be = root / "backup_engine"
            be.mkdir(parents=True)
            path = be / "bcd.py"
            path.write_text(
                "import subprocess\n"
                "def boom():\n"
                "    subprocess.run(['bcdedit', '/set', '{foo}', 'device', 'bar'])\n",
                encoding="utf-8",
            )
            rows = classify_file(root, path)
            self.assertTrue(
                any(
                    r.category == "DESTRUCTIVE_UNGUARDED"
                    and ("bcdedit" in r.sink.lower() or "bcdedit /set" in r.detail)
                    for r in rows
                )
            )

    def test_unguarded_mount_rw_run_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            be = root / "backup_engine"
            be.mkdir(parents=True)
            path = be / "mnt.py"
            path.write_text(
                "from common.command import run_command\n"
                "def boom():\n"
                "    run_command(['mount', '-o', 'rw', '/dev/x', '/mnt'], dry_run=False)\n",
                encoding="utf-8",
            )
            rows = classify_file(root, path)
            self.assertTrue(any(r.category == "DESTRUCTIVE_UNGUARDED" for r in rows))
            self.assertTrue(any("mount" in r.sink for r in rows))


class DestructiveDocsWarningTests(unittest.TestCase):
    def test_docs_destructive_is_warning_not_fail_status(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            doc = root / "docs"
            doc.mkdir(parents=True)
            (doc / "sample.py").write_text(
                "import shutil\n"
                "def example():\n"
                "    shutil.rmtree('/example')\n",
                encoding="utf-8",
            )
            st, rows, meta = run_destructive_audit(root)
            self.assertEqual(st, "PASS_WITH_WARNINGS")
            self.assertTrue(meta.get("complete"))
            self.assertTrue(any(r.rel_path.startswith("docs/") for r in rows))


class IncompleteDestructiveAuditTests(unittest.TestCase):
    def test_safety_audit_fails_when_destructive_incomplete_flag(self):
        fake_meta = {"complete": False, "engine_version": 0, "tracked_sinks": []}
        with patch.object(sa, "run_destructive_audit", return_value=("PASS_WITH_WARNINGS", [], fake_meta)):
            with tempfile.TemporaryDirectory() as td:
                status, report = sa.run_safety_audit(report_path=Path(td) / "out.json")
        self.assertEqual(status, "FAIL")
        self.assertFalse(report["destructive_command_audit"]["complete"])


class PolicyAuditTests(unittest.TestCase):
    def test_authorize_restore_execution_missing_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rs = root / "restore_engine"
            rs.mkdir(parents=True)
            (rs / "run_restore.py").write_text(
                "# --apply\n# --confirm\n# --phrase\n"
                "def main():\n"
                "    from restore_engine.restore_executor import RestoreExecutor\n"
                "    RestoreExecutor(None)\n",
                encoding="utf-8",
            )
            findings = cp.check_run_restore(repo_root=root)
            self.assertTrue(any(f.check_id == "RS1" and f.severity == "FAIL" for f in findings))

    def test_windows_agent_partclone_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wa = root / "windows_agent"
            wa.mkdir(parents=True)
            (wa / "leak.py").write_text("# x\nBAD = 'partclone.ntfs -r'\n", encoding="utf-8")
            findings = cp.windows_agent_checks(repo_root=root)
            self.assertTrue(findings)
            self.assertEqual(findings[0].severity, "FAIL")


class ReportIntegrationTests(unittest.TestCase):
    def test_safety_audit_writes_report_with_destructive_meta(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "safety_audit_report.json"
            status, report = sa.run_safety_audit(report_path=out)
            self.assertIn(status, ("PASS", "PASS_WITH_WARNINGS", "FAIL"))
            self.assertTrue(out.is_file())
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], status)
            self.assertIn("pattern_scan", data)
            dblock = data["destructive_command_audit"]
            self.assertIn("complete", dblock)
            self.assertTrue(dblock["complete"])
            self.assertIn("findings", dblock)

    def test_prod_unguarded_triggers_audit_fail_and_safety_fail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            be = root / "backup_engine"
            be.mkdir(parents=True)
            (be / "bad.py").write_text(
                "from common.command import run_command\n"
                "def boom():\n"
                "    run_command(['sh', '-c', 'exit 1'], dry_run=False)\n",
                encoding="utf-8",
            )
            cfg = json.loads(
                (sa.REPO_ROOT / "tools" / "forbidden_audit_patterns.json").read_text(encoding="utf-8"),
            )
            hits = sa.scan_text_patterns(root, cfg)
            destructive_status, destructive_rows, meta = run_destructive_audit(root)
            self.assertTrue(meta["complete"])
            self.assertEqual(destructive_status, "FAIL")
            self.assertTrue(any(r.category == "DESTRUCTIVE_UNGUARDED" for r in destructive_rows))
            final = sa._aggregate_status(hits, "PASS", destructive_status)
            self.assertEqual(final, "FAIL")


if __name__ == "__main__":
    unittest.main()
