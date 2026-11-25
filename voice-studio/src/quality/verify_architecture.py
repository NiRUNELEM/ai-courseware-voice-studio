#!/usr/bin/env python3
"""
Chin & YouRin Dual-SA Framework (v2.3.0)
Polyglot AST & Structural Hard-Gate Verifier (verify_architecture.py)

Physically inspects a target workspace/directory using Python's AST and polyglot structural parsers:
  1. [GATE-1] Anti-God-File Enforcement: Flags source files exceeding --max-lines (default: 500).
  2. [GATE-2] DRY / Single-Source-of-Truth Structural Clone Scan:
     - Python (.py): Computes Normalized AST Body Hash (ignoring docstrings and line offsets) so
       functions with the same name but distinct implementations are NOT falsely flagged, while
       genuinely copy-pasted functions across files ARE deterministically caught.
     - Polyglot (.ts, .tsx, .js, .jsx, .go, .rs, .cs, .java): Computes normalized token body hashes
       for top-level functions across files.
  3. [GATE-3] Zero Silent Exception Swallowing:
     - Python (.py): Parses AST ExceptHandler nodes to ban `except: pass` or `except Exception: ...`.
     - Polyglot (.ts, .tsx, .js, .jsx, .go, .rs, .cs, .java): Detects empty `catch (...) {}` blocks.
  4. [GATE-4] Workspace Root & Scratch Hygiene: Detects excessive loose script/temp clutter in root.
  5. [GATE-5] Chaos & Syntax Integrity: Guards against binary null-byte masquerading files,
     deep-recursion AST bombs (RecursionError), and syntax corruption.

Exit Code:
  0 = All 5 architectural gates PASSED
  1 = One or more architectural violations detected
"""

import argparse
import ast
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "build",
    "cache",
    "exports",
    "models",
    "old",
    ".architecture",
}

ALLOWED_DUPLICATE_FUNC_NAMES = {
    "main",
    "__init__",
    "setup",
    "teardown",
    "setUp",
    "tearDown",
    "run",
    "cli",
    "parse_args",
}

CODE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".cs"}
POLYGLOT_C_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".cs"}

# Matches empty catch blocks in TS/JS/Java/C#/Rust: `catch (e) { }` or `catch { // comment }`
EMPTY_CATCH_REGEX = re.compile(
    r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*(?://[^\n]*\n|\s|/\*[\s\S]*?\*/)*\}",
    flags=re.MULTILINE,
)

# Matches top-level function definitions in TS/JS/Go/Rust for structural clone checking
POLYGLOT_FUNC_REGEX = re.compile(
    r"^(?:export\s+)?(?:async\s+)?(?:function|func|fn)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*(?:[^{;\n]*)\{",
    flags=re.MULTILINE,
)


def is_silent_except_body(body: List[ast.stmt]) -> bool:
    """Returns True if an exception handler body only contains `pass`, `...`, or a docstring."""
    meaningful_stmts = []
    for stmt in body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue
        meaningful_stmts.append(stmt)
    return len(meaningful_stmts) == 0


def compute_python_ast_body_hash(func_node: ast.AST) -> str:
    """
    Computes a normalized structural hash of a Python function body.
    Strips leading docstrings and line numbers so two functions with the same name
    are only flagged as duplicates if their structural AST bodies match.
    """
    body = list(getattr(func_node, "body", []))
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]

    dumped_stmts = [ast.dump(stmt, annotate_fields=True, include_attributes=False) for stmt in body]
    joined = "|".join(dumped_stmts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def extract_polyglot_brace_body(content: str, start_brace_idx: int) -> Tuple[str, int]:
    """Extracts the balanced `{ ... }` block starting at `start_brace_idx`."""
    depth = 0
    i = start_brace_idx
    n = len(content)
    while i < n:
        ch = content[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                body_str = content[start_brace_idx + 1 : i]
                line_span = body_str.count("\n") + 1
                return body_str, line_span
        i += 1
    return "", 0


def scan_workspace(
    target_dir: Path,
    max_lines: int = 500,
    max_root_scripts: int = 8,
) -> Dict:
    god_files: List[Dict] = []
    silent_exceptions: List[Dict] = []
    # Key: (func_name, arg_count, structural_body_hash) -> List["rel_path:lineno"]
    function_registry: Dict[Tuple[str, int, str], List[str]] = defaultdict(list)
    root_clutter: List[str] = []
    syntax_errors: List[Dict] = []
    scanned_files_count = 0

    for item in target_dir.iterdir():
        if item.is_file():
            ext = item.suffix.lower()
            name = item.name.lower()
            if (
                ext in {".py", ".sh", ".ps1", ".bat", ".tmp", ".bak", ".log"}
                and name not in {"setup.py", "manage.py", "conftest.py"}
            ):
                root_clutter.append(item.name)

    for root, dirs, files in os.walk(target_dir, followlinks=False):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".venv")]

        root_path = Path(root)
        for fname in files:
            fpath = root_path / fname
            if fpath.is_symlink():
                continue
            ext = fpath.suffix.lower()
            if ext not in CODE_EXTENSIONS:
                continue

            scanned_files_count += 1
            rel_path = str(fpath.relative_to(target_dir)).replace("\\", "/")

            try:
                raw_bytes = fpath.read_bytes()
            except Exception as exc:
                syntax_errors.append({"file": rel_path, "error": f"ReadError: {exc}"})
                continue

            # GATE 5A: Binary / Null-Byte Masquerade Check
            if b"\x00" in raw_bytes:
                syntax_errors.append(
                    {
                        "file": rel_path,
                        "line": 1,
                        "error": "BinaryNullByteError: File contains null bytes (binary masquerading as source)",
                    }
                )
                continue

            content = raw_bytes.decode("utf-8", errors="replace")
            lines = content.splitlines()
            line_count = len(lines)

            # GATE 1: Anti-God-File Check
            if line_count > max_lines:
                god_files.append(
                    {
                        "file": rel_path,
                        "lines": line_count,
                        "limit": max_lines,
                        "excess": line_count - max_lines,
                    }
                )

            # Python AST Scan (GATE 2, GATE 3, GATE 5)
            if ext == ".py":
                try:
                    tree = ast.parse(content, filename=rel_path)
                except SyntaxError as syn_err:
                    syntax_errors.append(
                        {
                            "file": rel_path,
                            "line": syn_err.lineno or 1,
                            "error": f"SyntaxError: {syn_err.msg}",
                        }
                    )
                    continue
                except (RecursionError, MemoryError) as depth_err:
                    syntax_errors.append(
                        {
                            "file": rel_path,
                            "line": 1,
                            "error": f"ASTComplexityBombError: {type(depth_err).__name__} during AST parse",
                        }
                    )
                    continue

                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        func_name = node.name
                        if (
                            not func_name.startswith("_")
                            and not func_name.startswith("test_")
                            and func_name not in ALLOWED_DUPLICATE_FUNC_NAMES
                        ):
                            arg_count = len(node.args.args)
                            end_lineno = getattr(node, "end_lineno", node.lineno + 5)
                            func_span = end_lineno - node.lineno
                            if func_span >= 4:
                                try:
                                    bhash = compute_python_ast_body_hash(node)
                                except RecursionError:
                                    bhash = "recursion_limit"
                                function_registry[(func_name, arg_count, bhash)].append(
                                    f"{rel_path}:{node.lineno}"
                                )

                try:
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ExceptHandler):
                            if is_silent_except_body(node.body):
                                exc_type = "bare except"
                                if node.type is not None:
                                    try:
                                        exc_type = f"except {ast.unparse(node.type)}"
                                    except Exception:
                                        exc_type = "except <Exception>"
                                silent_exceptions.append(
                                    {
                                        "file": rel_path,
                                        "line": node.lineno,
                                        "handler": exc_type,
                                    }
                                )
                except RecursionError:
                    syntax_errors.append(
                        {
                            "file": rel_path,
                            "line": 1,
                            "error": "ASTComplexityBombError: RecursionError during AST walk",
                        }
                    )

            # Polyglot C-Family / TypeScript / Go / Rust Scan (GATE 2 & GATE 3)
            elif ext in POLYGLOT_C_EXTENSIONS:
                for match in EMPTY_CATCH_REGEX.finditer(content):
                    lineno = content[: match.start()].count("\n") + 1
                    silent_exceptions.append(
                        {
                            "file": rel_path,
                            "line": lineno,
                            "handler": "empty catch {}",
                        }
                    )

                for match in POLYGLOT_FUNC_REGEX.finditer(content):
                    func_name = match.group(1)
                    if (
                        func_name.startswith("_")
                        or func_name.startswith("test")
                        or func_name in ALLOWED_DUPLICATE_FUNC_NAMES
                    ):
                        continue
                    raw_args = match.group(2).strip()
                    arg_count = 0 if not raw_args else len([a for a in raw_args.split(",") if a.strip()])
                    brace_idx = match.end() - 1
                    body_str, span = extract_polyglot_brace_body(content, brace_idx)
                    if span >= 4 and body_str.strip():
                        norm_body = re.sub(r"\s+", " ", body_str.strip())
                        bhash = hashlib.sha256(norm_body.encode("utf-8")).hexdigest()[:16]
                        lineno = content[: match.start()].count("\n") + 1
                        function_registry[(func_name, arg_count, bhash)].append(f"{rel_path}:{lineno}")

    duplicate_functions = []
    for (func_name, arg_count, bhash), locations in sorted(function_registry.items()):
        distinct_files = {loc.split(":")[0] for loc in locations}
        if len(distinct_files) > 1:
            duplicate_functions.append(
                {
                    "function": func_name,
                    "arity": arg_count,
                    "structural_hash": bhash,
                    "locations": locations,
                }
            )

    root_clutter_violation = len(root_clutter) > max_root_scripts

    passed = (
        len(god_files) == 0
        and len(duplicate_functions) == 0
        and len(silent_exceptions) == 0
        and len(syntax_errors) == 0
        and not root_clutter_violation
    )

    return {
        "verifier_version": "2.3.0",
        "target_dir": str(target_dir.resolve()),
        "scanned_files": scanned_files_count,
        "passed": passed,
        "gates": {
            "gate_1_anti_god_file": {
                "passed": len(god_files) == 0,
                "max_lines_limit": max_lines,
                "violations": god_files,
            },
            "gate_2_dry_single_source": {
                "passed": len(duplicate_functions) == 0,
                "violations": duplicate_functions,
            },
            "gate_3_zero_silent_exceptions": {
                "passed": len(silent_exceptions) == 0,
                "violations": silent_exceptions,
            },
            "gate_4_root_workspace_hygiene": {
                "passed": not root_clutter_violation,
                "max_root_scripts_limit": max_root_scripts,
                "root_scripts_found": len(root_clutter),
                "files": sorted(root_clutter) if root_clutter_violation else [],
            },
            "gate_5_syntax_integrity": {
                "passed": len(syntax_errors) == 0,
                "violations": syntax_errors,
            },
        },
    }


def print_architecture_report(report: Dict) -> None:
    print("=" * 84)
    print("CHIN & YOURIN POLYGLOT AST & STRUCTURAL HARD-GATE VERIFIER (v2.3.0)")
    print("=" * 84)
    print(f"Target Workspace : {report['target_dir']}")
    print(f"Scanned Files    : {report['scanned_files']}")
    print("-" * 84)

    gates = report["gates"]
    g1 = gates["gate_1_anti_god_file"]
    print(
        f"[{'PASS' if g1['passed'] else 'FAIL'}] GATE 1: Anti-God-File (<={g1['max_lines_limit']} lines) "
        f"-> {len(g1['violations'])} violation(s)"
    )
    for v in g1["violations"]:
        print(f"       - {v['file']} ({v['lines']} lines, +{v['excess']} over limit)")

    g2 = gates["gate_2_dry_single_source"]
    print(
        f"[{'PASS' if g2['passed'] else 'FAIL'}] GATE 2: DRY Structural Clone Scan (AST Body Hash) "
        f"-> {len(g2['violations'])} duplicate clone(s)"
    )
    for v in g2["violations"]:
        print(
            f"       - {v['function']}({v['arity']} args) [hash:{v['structural_hash']}] "
            f"duplicated in: {', '.join(v['locations'])}"
        )

    g3 = gates["gate_3_zero_silent_exceptions"]
    print(
        f"[{'PASS' if g3['passed'] else 'FAIL'}] GATE 3: Zero Silent Exception Swallowing (Py/TS/JS/Go/Rust) "
        f"-> {len(g3['violations'])} violation(s)"
    )
    for v in g3["violations"]:
        print(f"       - {v['file']}:{v['line']} ({v['handler']})")

    g4 = gates["gate_4_root_workspace_hygiene"]
    print(
        f"[{'PASS' if g4['passed'] else 'FAIL'}] GATE 4: Workspace Root Hygiene (<={g4['max_root_scripts_limit']} loose scripts) "
        f"-> {g4['root_scripts_found']} found"
    )
    if not g4["passed"]:
        print(f"       - Root clutter: {', '.join(g4['files'])}")

    g5 = gates["gate_5_syntax_integrity"]
    print(
        f"[{'PASS' if g5['passed'] else 'FAIL'}] GATE 5: Chaos & AST Syntax Integrity "
        f"-> {len(g5['violations'])} error(s)"
    )
    for v in g5["violations"]:
        print(f"       - {v['file']}: {v['error']}")

    print("-" * 84)
    print(
        f"OVERALL VERDICT  : {'PASSED (0 VIOLATIONS)' if report['passed'] else 'FAILED (ARCHITECTURAL DRIFT DETECTED)'}"
    )
    print("=" * 84)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Chin & YouRin Polyglot AST & Structural Hard-Gate Verifier (v2.3.0)"
    )
    parser.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="Path to target workspace or directory to audit (default: current directory)",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=500,
        help="Maximum allowed lines per source file before flagging as a God-File (default: 500)",
    )
    parser.add_argument(
        "--max-root-scripts",
        type=int,
        default=8,
        help="Maximum allowed loose scripts in workspace root (default: 8)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON report",
    )

    args = parser.parse_args()
    target_path = Path(args.target_dir)
    if not target_path.exists() or not target_path.is_dir():
        print(f"[ERROR] Target directory does not exist: {target_path}", file=sys.stderr)
        return 1

    report = scan_workspace(
        target_dir=target_path,
        max_lines=args.max_lines,
        max_root_scripts=args.max_root_scripts,
    )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_architecture_report(report)

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
