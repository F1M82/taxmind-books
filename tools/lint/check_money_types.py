"""AST-based lint check for money-handling violations.

Enforces the rules from `docs/MONEY.md` §"Enforcement / Layer 2":

  M001  float annotation on a money-named attribute
  M002  Numeric(...) column with non-(15, 2) precision
  M003  Decimal(<float-literal>) — float-to-Decimal conversion
  M004  paise math (int * 100 / int / 100 near a money name)
  M005  schema in app/schemas/ inheriting BaseModel directly instead
        of TaxMindBooksBase

Run:
    python tools/lint/check_money_types.py backend/app

Exit code 0 if clean, 1 if any violation. CI invokes this on every PR.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

# Names that almost certainly hold money. Used for both annotation and
# paise-math heuristics.
MONEY_NAME_RE = re.compile(
    r"(?i)(amount|balance|total|price|tax|gst|tds|cgst|sgst|igst|fee|charge)"
)

# Required precision/scale for any Numeric column that holds money,
# per docs/MONEY.md §"The canonical column type".
EXPECTED_PRECISION = 15
EXPECTED_SCALE = 2

# Heuristic: paise math multiplies / divides money by this constant.
PAISE_FACTOR = 100


@dataclass
class Violation:
    code: str
    path: Path
    line: int
    col: int
    message: str

    def render(self) -> str:
        return f"{self.path}:{self.line}:{self.col}: {self.code} {self.message}"


def _is_money_name(name: str) -> bool:
    return bool(MONEY_NAME_RE.search(name))


def _is_float_annotation(node: ast.expr | None) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Name) and node.id == "float":
        return True
    # `float | None`, `Optional[float]` etc.
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _is_float_annotation(node.left) or _is_float_annotation(node.right)
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id in {"Optional", "Union"}
    ):
        slice_node = node.slice
        if isinstance(slice_node, ast.Tuple):
            return any(_is_float_annotation(e) for e in slice_node.elts)
        return _is_float_annotation(slice_node)
    return False


def _numeric_call_args(node: ast.Call) -> tuple[int | None, int | None]:
    """Return (precision, scale) from a Numeric(p, s) call, when literal."""
    p = s = None
    if node.args:
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, int):
            p = first.value
    if len(node.args) >= 2:  # noqa: PLR2004 — Numeric() takes (precision, scale)
        second = node.args[1]
        if isinstance(second, ast.Constant) and isinstance(second.value, int):
            s = second.value
    for kw in node.keywords:
        if kw.arg == "precision" and isinstance(kw.value, ast.Constant):
            p = kw.value.value if isinstance(kw.value.value, int) else p
        if kw.arg == "scale" and isinstance(kw.value, ast.Constant):
            s = kw.value.value if isinstance(kw.value.value, int) else s
    return p, s


class _Checker(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: list[Violation] = []
        self._in_schemas = "schemas" in path.parts and "tests" not in path.parts

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        target = node.target
        name: str | None = None
        if isinstance(target, ast.Name):
            name = target.id
        elif isinstance(target, ast.Attribute):
            name = target.attr
        if name and _is_money_name(name) and _is_float_annotation(node.annotation):
            self.violations.append(
                Violation(
                    code="M001",
                    path=self.path,
                    line=node.lineno,
                    col=node.col_offset,
                    message=(
                        f"`{name}` annotated as float; money must be Decimal "
                        f"(use schemas.Money / SignedMoney or money_column)"
                    ),
                )
            )
        self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> None:
        if node.annotation and _is_money_name(node.arg) and _is_float_annotation(
            node.annotation
        ):
            self.violations.append(
                Violation(
                    code="M001",
                    path=self.path,
                    line=node.lineno,
                    col=node.col_offset,
                    message=f"`{node.arg}` parameter annotated as float; must be Decimal",
                )
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # M002: Numeric(p, s) with wrong precision/scale
        if isinstance(node.func, ast.Name) and node.func.id == "Numeric":
            p, s = _numeric_call_args(node)
            if (
                p is not None
                and s is not None
                and (p != EXPECTED_PRECISION or s != EXPECTED_SCALE)
            ):
                self.violations.append(
                    Violation(
                        code="M002",
                        path=self.path,
                        line=node.lineno,
                        col=node.col_offset,
                        message=(
                            f"Numeric({p}, {s}) used for what looks like money; "
                            f"use MoneyColumn from app.core.money"
                        ),
                    )
                )

        # M003: Decimal(<float-literal>)
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "Decimal"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, float)
        ):
            self.violations.append(
                Violation(
                    code="M003",
                    path=self.path,
                    line=node.lineno,
                    col=node.col_offset,
                    message=(
                        "Decimal(<float>) silently loses precision; "
                        "wrap the literal in str() or use a string literal"
                    ),
                )
            )

        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        # M004: heuristic — `<money_name> * 100`, `<money_name> / 100`,
        # `100 * <money_name>` near a money-typed identifier.
        if isinstance(node.op, ast.Mult | ast.Div):
            const_side, name_side = None, None
            if isinstance(node.left, ast.Constant) and node.left.value == PAISE_FACTOR:
                const_side, name_side = node.left, node.right
            elif (
                isinstance(node.right, ast.Constant)
                and node.right.value == PAISE_FACTOR
            ):
                const_side, name_side = node.right, node.left
            if (
                const_side is not None
                and isinstance(name_side, ast.Name)
                and _is_money_name(name_side.id)
            ):
                self.violations.append(
                    Violation(
                        code="M004",
                        path=self.path,
                        line=node.lineno,
                        col=node.col_offset,
                        message=(
                            f"`{name_side.id} * 100` / `/ 100` looks like "
                            f"paise math; use Decimal arithmetic instead"
                        ),
                    )
                )
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if self._in_schemas:
            base_names = [b.id for b in node.bases if isinstance(b, ast.Name)]
            # Allow TaxMindBooksBase itself to inherit BaseModel.
            if node.name == "TaxMindBooksBase":
                self.generic_visit(node)
                return
            if "BaseModel" in base_names and "TaxMindBooksBase" not in base_names:
                self.violations.append(
                    Violation(
                        code="M005",
                        path=self.path,
                        line=node.lineno,
                        col=node.col_offset,
                        message=(
                            f"`{node.name}` inherits BaseModel directly; "
                            f"schemas in app/schemas/ must inherit TaxMindBooksBase"
                        ),
                    )
                )
        self.generic_visit(node)


def check_path(path: Path) -> list[Violation]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [
            Violation(
                code="M000",
                path=path,
                line=exc.lineno or 0,
                col=exc.offset or 0,
                message=f"syntax error: {exc.msg}",
            )
        ]
    checker = _Checker(path)
    checker.visit(tree)
    return checker.violations


def iter_python_files(roots: Iterable[Path]) -> Iterator[Path]:
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            yield root
        elif root.is_dir():
            for p in root.rglob("*.py"):
                if any(part in {".venv", "venv", "__pycache__", "alembic"} for part in p.parts):
                    continue
                yield p


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_money_types.py <path> [<path> ...]", file=sys.stderr)
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
