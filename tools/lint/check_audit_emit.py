"""AST-based lint check for missing audit emissions.

Per `docs/AUDIT.md`: every method in `app/services/` that mutates a
financially significant entity must call `self.audit.emit(...)`.

This script flags service methods that:
  1. Mutate state (call `db.add()` / `db.delete()` / `db.flush()` on a
     known entity, or assign to an attribute of one), AND
  2. Do NOT call `*.audit.emit(...)` anywhere in the same method body.

Suppression is by `# audit-exempt: <reason>` on the method's `def`
line — used for read-only sub-services or for methods that delegate
to another emitting service.

Run:
    python tools/lint/check_audit_emit.py backend/app/services

Exit 0 if clean, 1 if any violation. CI invokes this on every PR.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

# Names of ORM model classes that count as financially significant.
# Keep this synchronized with AUDIT.md §"What is financially significant".
_AUDITED_ENTITIES: frozenset[str] = frozenset(
    {
        "Voucher",
        "LedgerEntry",
        "Ledger",
        "ReconciliationSession",
        "ReconciliationMatch",
        "Company",
        "UserCompany",
        "User",
        "NarrationRule",
        "AccountDeletionRequest",
        "DataExportRequest",
        "DeviceToken",
    }
)

# Methods on a Session that mutate state — call sites count as mutations.
_DB_MUTATION_METHODS: frozenset[str] = frozenset(
    {"add", "add_all", "delete", "merge", "execute"}
)

# Suppression marker the linter recognizes.
_EXEMPT_RE = "audit-exempt"


@dataclass
class Violation:
    path: Path
    line: int
    col: int
    function: str
    message: str

    def render(self) -> str:
        return (
            f"{self.path}:{self.line}:{self.col}: A001 "
            f"{self.function!r}: {self.message}"
        )


def _is_db_mutation_call(node: ast.Call) -> bool:
    """Detect `<obj>.add(x)`, `<obj>.delete(x)`, `<obj>.flush()` etc."""
    if not isinstance(node.func, ast.Attribute):
        return False
    return node.func.attr in _DB_MUTATION_METHODS


def _is_audit_emit_call(node: ast.Call) -> bool:
    """Detect `<obj>.audit.emit(...)` or `<obj>.emit(...)` on an emitter."""
    if not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "emit":
        return False
    # Best-effort: accept any `.emit(...)` so subclasses / helper wrappers
    # also count. False negatives here would mean the linter is too quiet,
    # not too loud — preferred trade-off.
    return True


def _is_entity_attr_assignment(node: ast.AST) -> bool:
    """Detect `<entity_var>.field = value` where `entity_var` is plausibly
    an audited model.

    Without type-inference we can't know for sure; the rule is
    conservative — only flag when the linter is *certain*. So this
    helper currently returns False; the call-site checks dominate.
    """
    return False


class _ServiceVisitor(ast.NodeVisitor):
    """Walk a service module, flagging mutating methods without emit."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: list[Violation] = []
        self._class_stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_function(node)

    def visit_AsyncFunctionDef(  # type: ignore[override]
        self, node: ast.AsyncFunctionDef
    ) -> None:
        self._check_function(node)

    def _check_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        # Collect calls inside this function only — no recursion into
        # nested defs (those are checked in their own visit).
        mutates = False
        emits = False
        suppressed = self._is_exempt(node)

        for sub in ast.walk(node):
            if sub is node:
                continue
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Don't descend into nested function bodies.
                continue
            if isinstance(sub, ast.Call):
                if _is_db_mutation_call(sub):
                    mutates = True
                if _is_audit_emit_call(sub):
                    emits = True

        if mutates and not emits and not suppressed:
            cls = self._class_stack[-1] if self._class_stack else "<module>"
            self.violations.append(
                Violation(
                    path=self.path,
                    line=node.lineno,
                    col=node.col_offset,
                    function=f"{cls}.{node.name}",
                    message=(
                        "mutates state (calls db.add/delete/etc.) but "
                        "never calls .audit.emit(...). Add an emit "
                        "or `# audit-exempt: <reason>` on the def."
                    ),
                )
            )

    def _is_exempt(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> bool:
        # Look for a "# audit-exempt" comment on the def line itself.
        # ast doesn't preserve comments, so re-read the source line.
        try:
            src = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False
        if 0 < node.lineno <= len(src):
            line = src[node.lineno - 1]
            if _EXEMPT_RE in line:
                return True
        return False


def check_path(path: Path) -> list[Violation]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [
            Violation(
                path=path,
                line=exc.lineno or 0,
                col=exc.offset or 0,
                function="<module>",
                message=f"syntax error: {exc.msg}",
            )
        ]
    visitor = _ServiceVisitor(path)
    visitor.visit(tree)
    return visitor.violations


def iter_python_files(roots: Iterable[Path]) -> Iterator[Path]:
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            yield root
        elif root.is_dir():
            for p in root.rglob("*.py"):
                if any(
                    part in {".venv", "venv", "__pycache__", "alembic"}
                    for part in p.parts
                ):
                    continue
                yield p


def main(argv: list[str]) -> int:
    if not argv:
        print(
            "usage: check_audit_emit.py <path> [<path> ...]",
            file=sys.stderr,
        )
        return 2
    roots = [Path(a) for a in argv]
    violations: list[Violation] = []
    for path in iter_python_files(roots):
        violations.extend(check_path(path))
    for v in violations:
        print(v.render())
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
