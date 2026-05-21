#!/usr/bin/env python3
"""AST 기반 파괴적 sink 분류(run_command/run_readonly/subprocess/파일 I/O).

미완성( DESTRUCTIVE_AUDIT_COMPLETE=False )이면 safety_audit 이 FAIL 처리합니다."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

DESTRUCTIVE_AUDIT_COMPLETE: bool = True
DESTRUCTIVE_AUDIT_ENGINE_VERSION: int = 5

TRACKED_SINK_GROUPS: Tuple[str, ...] = (
    "partclone.ntfs",
    "sgdisk",
    "sgdisk --load-backup",
    "bcdedit /set",
    "mount",
    "mount -o rw",
    "umount",
    "mkfs",
    "format",
    "shutil.rmtree",
    "pathlib.Path.unlink",
    "os.remove",
    "os.unlink",
    "write_text",
    "write_bytes",
    "subprocess.run",
    "run_command",
    "run_readonly",
)

_SCAN_TOP_LEVEL_DIRS: Tuple[str, ...] = (
    "backup_engine",
    "restore_engine",
    "recovery_runtime",
    "rollback",
    "partition_manager",
    "boot_manager",
    "common",
    "validation",
    "grub",
    "windows_agent",
)

GUARD_CALL_NAMES: frozenset[str] = frozenset(
    {
        "_assert_authorized",
        "_run_confirmed",
        "_ensure_write_allowed",
        "verify_phrase",
        "authorize_restore_execution",
        "prompt_phrase",
    }
)

_POLICY_APPLY_FUNCTIONS: Set[Tuple[str, str]] = {
    # 게이트/진단 스크립트: diagnostics/*.json 기록만 (파티션/EFI 파괴 경로 아님)
    ("tools/pre_destructive_gate.py", "main"),
    ("tools/release_readiness_check.py", "main"),
    ("tools/final_release_gate.py", "main"),
    ("backup_engine/manifest.py", "write_recovery_manifest"),
    ("backup_engine/manifest.py", "_invalidate_backup_on_failure"),
    ("backup_engine/backup_state.py", "mark_incomplete_backup"),
    ("backup_engine/backup_state.py", "clear_incomplete_backup"),
    ("restore_engine/restore_state.py", "save_recovery_state"),
    ("restore_engine/restore_state.py", "set_restore_stage"),
    ("restore_engine/restore_state.py", "mark_restore_started"),
    ("restore_engine/restore_state.py", "mark_restore_success"),
    ("restore_engine/restore_state.py", "mark_restore_failed"),
    ("grub/generate_grub_config.py", "write_grub_config"),
    ("windows_agent/event_monitor.py", "save_snapshot"),
    ("recovery_runtime/actions.py", "_rollback_delete"),
    ("rollback/efi_rollback.py", "rollback_efi"),
    ("rollback/gpt_rollback.py", "rollback_gpt"),
}


@dataclass(frozen=True)
class DestructiveFinding:
    rel_path: str
    lineno: int
    category: str
    sink: str
    detail: str


def norm_rel(rel: str) -> str:
    return rel.replace("\\", "/")


def is_docs_or_tests_path(rel: str) -> bool:
    r = norm_rel(rel)
    return r.startswith("docs/") or r.startswith("tests/") or "/tests/" in r


def counts_as_fail_bucket(finding: DestructiveFinding) -> bool:
    return finding.category == "DESTRUCTIVE_UNGUARDED" and not is_docs_or_tests_path(finding.rel_path)


def _parent_map(tree: ast.Module) -> Dict[ast.AST, ast.AST]:
    parents: Dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            parents[ch] = node
    return parents


def _enclosing_function(parents: Dict[ast.AST, ast.AST], node: ast.AST) -> Optional[ast.FunctionDef]:
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur  # type: ignore[return-value]
        cur = parents.get(cur)
    return None


def _lineno(n: ast.AST) -> int:
    return int(getattr(n, "lineno", 0) or 0)


def _qualified_call(call: ast.Call) -> Optional[str]:
    parts: List[str] = []
    cur: ast.AST | None = call.func
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _prior_guard(fn: ast.FunctionDef, sink_line: int) -> bool:
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            if _lineno(n) and _lineno(n) < sink_line:
                q = _qualified_call(n)
                if q:
                    leaf = q.rsplit(".", 1)[-1]
                    if leaf in GUARD_CALL_NAMES:
                        return True
    return False


def _stmt_list_has(stmts: Sequence[ast.stmt], sink: ast.AST) -> bool:
    return any(any(w is sink for w in ast.walk(st)) for st in stmts)


def _dry_run_pred(expr: ast.expr) -> bool:
    if isinstance(expr, ast.Name) and expr.id == "dry_run":
        return True
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
        return isinstance(expr.operand, ast.Name) and expr.operand.id == "dry_run"
    if isinstance(expr, ast.Compare) and isinstance(expr.left, ast.Name) and expr.left.id == "dry_run":
        return any(isinstance(o, ast.Eq) for o in expr.ops) and any(
            isinstance(c, ast.Constant) and c.value is False for c in expr.comparators
        )
    return False


def _guarded_dry_else(parents: Dict[ast.AST, ast.AST], sink: ast.AST) -> bool:
    cur: ast.AST | None = sink
    while cur is not None:
        pred = parents.get(cur)
        if isinstance(pred, ast.If) and _dry_run_pred(pred.test):
            if pred.orelse and _stmt_list_has(pred.orelse, sink):
                return True
            if isinstance(pred.test, ast.UnaryOp) and _stmt_list_has(pred.body, sink):
                return True
        cur = pred
    return False


def _cls_name_above(parents: Dict[ast.AST, ast.AST], sink: ast.AST) -> Optional[str]:
    cur = parents.get(sink)
    while cur:
        if isinstance(cur, ast.ClassDef):
            return cur.name
        cur = parents.get(cur)
    return None


def _writeguard_safe(parents: Dict[ast.AST, ast.AST], call: ast.Call, fn: ast.FunctionDef) -> bool:
    if _cls_name_above(parents, call) != "WriteGuard":
        return False
    return _prior_guard(fn, _lineno(call))


def _policy_apply(rel: str, fn: Optional[ast.FunctionDef]) -> bool:
    if fn is None:
        return False
    rp = norm_rel(rel)
    return (rp, fn.name) in _POLICY_APPLY_FUNCTIONS


def _tools_safe(rel: str, fn: Optional[ast.FunctionDef]) -> bool:
    if fn is None:
        return False
    rp = norm_rel(rel)
    if not rp.startswith("tools/"):
        return False
    return Path(rp).name in {"collect_diagnostics.py", "run_integration_checks.py"}


def _is_run_command(call: ast.Call) -> bool:
    q = _qualified_call(call)
    return q == "run_command" or (q or "").endswith(".run_command")


def _is_run_readonly(call: ast.Call) -> bool:
    q = _qualified_call(call)
    return q == "run_readonly" or (q or "").endswith(".run_readonly")


def _is_subprocess_run(call: ast.Call) -> bool:
    q = _qualified_call(call)
    return q == "subprocess.run"


def _kw(call: ast.Call, name: str) -> Optional[ast.expr]:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _literal_concat(exp: ast.AST | None, depth: int = 14) -> Optional[str]:
    if depth <= 0 or exp is None:
        return None
    if isinstance(exp, ast.Constant) and isinstance(exp.value, str):
        return exp.value
    if isinstance(exp, ast.JoinedStr):
        parts: List[str] = []
        for v in exp.values:
            s = _literal_concat(v, depth - 1)  # type: ignore[arg-type]
            if s is None:
                return None
            parts.append(s)
        return "".join(parts)
    if isinstance(exp, ast.BinOp) and isinstance(exp.op, ast.Add):
        l = _literal_concat(exp.left, depth - 1)
        r = _literal_concat(exp.right, depth - 1)
        if l is None or r is None:
            return None
        return l + r
    return None


def _literal_argv(call: ast.Call) -> List[str]:
    if not call.args:
        return []
    first = call.args[0]
    tokens: List[str] = []
    if isinstance(first, (ast.List, ast.Tuple)):
        for elt in first.elts:
            s = _literal_concat(elt)
            if s is not None:
                tokens.extend(s.strip().replace("\r", "").split())
    elif isinstance(first, ast.Constant) and isinstance(first.value, str):
        for ln in first.value.replace("\r", "").splitlines():
            tokens.extend([p for p in re.split(r"\s+", ln.strip()) if p])
    else:
        s = _literal_concat(first)
        if s:
            tokens.extend(s.strip().split())
    return tokens


def classify_blob(blob: str) -> List[str]:
    b = blob.lower()
    tags: List[str] = []
    if "partclone.ntfs" in b:
        tags.append("partclone.ntfs")
    if "sgdisk" in b:
        tags.append("sgdisk")
        if "load-backup" in b or "load_backup" in b.replace("-", "_"):
            tags.append("sgdisk --load-backup")
    comp = "".join(b.split())
    if re.search(r"bcdedit.*\/set", comp):
        tags.append("bcdedit /set")
    if "umount" in b:
        tags.append("umount")
    if "mkfs." in b:
        tags.append("mkfs")
    if re.search(r"\bformat\b", b):
        tags.append("format")
    if "mount" in b:
        tags.append("mount")
        if "-o" in b and (" rw" in b or "=rw" in b or ",rw" in b or re.search(r"-o\s*[a-z,]*rw", b)):
            tags.append("mount -o rw")
    return sorted(set(tags))


def _dry_infer(dry_kw: Optional[ast.expr]) -> Optional[bool]:
    if dry_kw is None:
        return True
    if isinstance(dry_kw, ast.Constant) and isinstance(dry_kw.value, bool):
        return dry_kw.value
    return None


def _confirmed_infer(cf_kw: Optional[ast.expr]) -> bool:
    if cf_kw is None:
        return False
    if isinstance(cf_kw, ast.Constant) and isinstance(cf_kw.value, bool):
        return cf_kw.value is True
    return True


class _AuditVisitor(ast.NodeVisitor):
    def __init__(self, rel: str, parents: Dict[ast.AST, ast.AST]) -> None:
        self.rel = norm_rel(rel)
        self.parents = parents
        self.rows: List[DestructiveFinding] = []

    def _emit(self, node: ast.AST, cat: str, sink: str, detail: str) -> None:
        self.rows.append(DestructiveFinding(self.rel, _lineno(node), cat, sink, detail))

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 — ast API

        enf = _enclosing_function(self.parents, node)

        if _is_run_readonly(node):

            argv = _literal_argv(node)

            tg = classify_blob(" ".join(argv))

            slug = ";".join(tg) if tg else "opaque"
            self._emit(node, "SAFE_READONLY", "run_readonly:" + slug, slug)

        elif _is_run_command(node):

            d = _dry_infer(_kw(node, "dry_run"))

            c = _confirmed_infer(_kw(node, "confirmed"))
            argv = _literal_argv(node)

            tg = classify_blob(" ".join(argv))


            slug = "+".join(tg) if tg else "generic"
            if d is True:


                cat = "DRY_RUN_ONLY"

            elif d is False:
                cat = "APPLY_GUARDED" if c else "DESTRUCTIVE_UNGUARDED"


            else:
                cat = "APPLY_GUARDED"
            self._emit(node, cat, "run_command:" + slug, slug)

        elif _is_subprocess_run(node):

            self._visit_subprocess(node, enf)


        elif _is_shutil_rmtree(node):


            self._fs_sink(node, enf, "shutil.rmtree")

        elif _is_os_unlink_rm(node):

            q = (_qualified_call(node) or "").lower()


            sink = "os.unlink" if "unlink" in q else "os.remove"
            self._fs_sink(node, enf, sink)

        elif isinstance(node.func, ast.Attribute):


            a = node.func.attr


            if a == "unlink":


                self._fs_sink(node, enf, "pathlib.Path.unlink")
            elif a == "write_text":


                self._fs_sink(node, enf, "write_text")
            elif a == "write_bytes":


                self._fs_sink(node, enf, "write_bytes")

        self.generic_visit(node)

    def _fs_sink(self, node: ast.Call, enf: Optional[ast.FunctionDef], sink: str) -> None:


        ln = _lineno(node)
        guards = False
        if enf and _prior_guard(enf, ln):
            guards = True
        guards = guards or _guarded_dry_else(self.parents, node)


        if enf:


            guards = guards or _writeguard_safe(self.parents, node, enf)


        guards = guards or _policy_apply(self.rel, enf)


        guards = guards or _tools_safe(self.rel, enf)
        cat = "APPLY_GUARDED" if guards else "DESTRUCTIVE_UNGUARDED"


        self._emit(node, cat, sink, sink)

    def _visit_subprocess(self, node: ast.Call, enf: Optional[ast.FunctionDef]) -> None:


        fname = enf.name if enf else ""


        argv = _literal_argv(node)


        blob_lc = " ".join(argv).lower()


        tg = classify_blob(blob_lc)


        comp = "".join(blob_lc.split())

        rp = self.rel

        # common/command.py 래퍼 내부
        if rp == "common/command.py":


            if fname == "run_readonly":
                self._emit(node, "SAFE_READONLY", "subprocess.run", "delegate run_readonly")
                return
            if fname == "run_command":
                self._emit(node, "APPLY_GUARDED", "subprocess.run", "delegate run_command")


                return


        # Windows 예약 태스크 등록 도구 — dry-run early return 존재
        if rp == "windows_agent/task_scheduler.py" and fname == "register_recovery_boot_monitor_task":
            self._emit(node, "APPLY_GUARDED", "subprocess.run", "powershell ScheduledTask tooling")
            return


        guarded = _guarded_dry_else(self.parents, node) or bool(enf and _policy_apply(rp, enf))


        # 조회만
        if "bcdedit" in blob_lc and "enum" in blob_lc and "/set" not in comp:
            self._emit(node, "SAFE_READONLY", "subprocess.run", "bcdedit enumeration")
            return


        # bcdedit /set
        if "bcdedit" in blob_lc and "/set" in comp:
            if guarded:


                self._emit(node, "APPLY_GUARDED", "bcdedit /set", "bcdedit /set guarded")


            else:
                self._emit(node, "DESTRUCTIVE_UNGUARDED", "bcdedit /set", "bcdedit /set subprocess")
            return


        # mount -o … rw …
        if "mount" in blob_lc and "-o" in blob_lc and (" rw" in blob_lc or "=rw" in blob_lc or ",rw" in blob_lc or re.search(r"-o\s*[a-z,+=]*rw\b", blob_lc)):
            if guarded or _policy_apply(rp, enf):
                self._emit(node, "APPLY_GUARDED", "mount -o rw", "mount rw")
            else:
                self._emit(node, "DESTRUCTIVE_UNGUARDED", "mount -o rw", "mount rw unguarded")


            return

        destructive = tg and (
            {"partclone.ntfs", "sgdisk", "sgdisk --load-backup", "format", "mkfs"} & set(tg)
        )
        if destructive and not guarded:
            self._emit(node, "DESTRUCTIVE_UNGUARDED", "subprocess.run", ";".join(tg))
            return



        self._emit(node, "APPLY_GUARDED", "subprocess.run", "opaque/other argv")


def _is_shutil_rmtree(call: ast.Call) -> bool:
    if not isinstance(call.func, ast.Attribute) or call.func.attr != "rmtree":
        return False
    v = call.func.value
    if isinstance(v, ast.Name) and v.id == "shutil":
        return True


    q = (_qualified_call(call) or "")


    return q.endswith("rmtree") and (q.startswith("shutil.") or q == "shutil.rmtree")


def _is_os_unlink_rm(call: ast.Call) -> bool:
    q = (_qualified_call(call) or "").lower()
    return (
        q in {"os.unlink", "os.remove"}
        or q.endswith(".unlink")
        or q.endswith(".remove")
    )


def classify_file(repo_root: Path, path: Path) -> List[DestructiveFinding]:
    rel = path.relative_to(repo_root).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return [
            DestructiveFinding(rel, 0, "PARSE_ERROR", "parse", "syntax error — skipped AST"),
        ]
    parents = _parent_map(tree)
    visitor = _AuditVisitor(rel, parents)
    visitor.visit(tree)
    return visitor.rows


def _iter_prod_modules(root: Path) -> Iterator[Path]:
    for top in _SCAN_TOP_LEVEL_DIRS:
        base = root / top
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if "tests" in path.parts or path.name.startswith("test_") or "__pycache__" in path.parts:
                continue
            yield path


def _iter_docs_tests(root: Path) -> Iterator[Path]:
    for pref in ("docs", "tests"):
        b = root / pref
        if not b.is_dir():
            continue
        for path in b.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            yield path


def _iter_other_tools(root: Path) -> Iterator[Path]:
    t = root / "tools"
    if not t.is_dir():
        return
    for path in t.rglob("*.py"):
        if path.name.startswith("test") or "__pycache__" in path.parts:
            continue
        if path.name in {"safety_audit.py", "code_policy_audit.py", "destructive_command_audit.py"}:
            continue
        yield path


def run_destructive_audit(
    repo_root: Path | None = None,
) -> Tuple[str, List[DestructiveFinding], Dict[str, Any]]:
    root = repo_root or REPO_ROOT
    meta: Dict[str, Any] = {
        "complete": DESTRUCTIVE_AUDIT_COMPLETE,
        "engine_version": DESTRUCTIVE_AUDIT_ENGINE_VERSION,
        "tracked_sinks": list(TRACKED_SINK_GROUPS),
    }
    if not DESTRUCTIVE_AUDIT_COMPLETE:
        return "FAIL", [], meta

    rows: List[DestructiveFinding] = []
    for p in _iter_prod_modules(root):
        rows.extend(classify_file(root, p))
    for p in _iter_docs_tests(root):
        rows.extend(classify_file(root, p))
    for p in _iter_other_tools(root):
        rows.extend(classify_file(root, p))

    prod_unguarded = any(counts_as_fail_bucket(r) for r in rows)
    prod_parse = any(
        r.category == "PARSE_ERROR" for r in rows if not is_docs_or_tests_path(r.rel_path)
    )
    docs_unguarded_warn = any(
        r.category == "DESTRUCTIVE_UNGUARDED" and is_docs_or_tests_path(r.rel_path) for r in rows
    )
    parse_warn_any = any(r.category == "PARSE_ERROR" for r in rows)

    if prod_unguarded:
        meta["fail_reason"] = "DESTRUCTIVE_UNGUARDED in production paths"
        status = "FAIL"
    elif prod_parse or docs_unguarded_warn or parse_warn_any:
        status = "PASS_WITH_WARNINGS"
    else:
        status = "PASS"

    meta["unguarded_production"] = sum(1 for r in rows if counts_as_fail_bucket(r))
    meta["warning_only_unguarded"] = sum(
        1 for r in rows if r.category == "DESTRUCTIVE_UNGUARDED" and is_docs_or_tests_path(r.rel_path)
    )
    return status, rows, meta


def serialize(rows: Sequence[DestructiveFinding]) -> List[Dict[str, Any]]:
    return [
        {
            "path": r.rel_path,
            "lineno": r.lineno,
            "category": r.category,
            "sink": r.sink,
            "detail": r.detail,
            "counts_as_fail": counts_as_fail_bucket(r),
        }
        for r in rows
    ]

if __name__ == "__main__":
    __st, __rows, __meta = run_destructive_audit()
    print(__st, len(__rows), __meta.get("complete"))
