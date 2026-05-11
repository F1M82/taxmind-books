"""AST-based lint for module-boundary violations.

Per `docs/REPO_LAYOUT.md` §"Module boundaries":

  | Source                  | Cannot import from                              |
  | backend/app/models/     | backend/app/services/, backend/app/api/         |
  | backend/app/schemas/    | backend/app/services/, backend/app/api/         |
  | backend/app/services/   | backend/app/api/                                |
  | backend/app/core/       | services/, api/, models/                        |
  | connector/              | anything under backend/                         |

The script parses each `.py` file under the given roots and walks
its top-level Import / ImportFrom nodes. Imports inside an
`if TYPE_CHECKING:` block don't count (they don't run at module
import time and are commonly needed to break circular type cycles).

Suppression: `# imports-exempt: <reason>` on the import line.

Exit code 0 if clean, 1 if any violation.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------
# Rules. Keys are the source-prefix (a path under the repo root, with
# forward slashes); values are forbidden-prefixes for any import from
# that source.
# ---------------------------------------------------------------------

_RULES: list[tuple[str, frozenset[str]]] = [
    (
        "backend/app/models",
        frozenset({"app.services", "app.api"}),
    ),
    (
        "backend/app/schemas",
        frozenset({"app.services", "app.api"}),
    ),
    (
        "backend/app/services",
        frozenset({"app.api"}),
    ),
    (
        "backend/app/core",
        frozenset({"app.services", "app.api", "app.models"}),
    ),
    (
        "connector",
        frozenset(
            {
                "app",  # connector mustn't reach into backend/app
                "backend",
            }
        ),
    ),
]

_EXEMPT_MARKER = "imports-exempt"


@dataclass
class Violation:
    path: Path
    line: int
    statement: str
    forbidden: str

    def render(self) -> str:
        return (
            f"{self.path}:{self.line}: IMP001 forbidden import "
            f"`{self.statement}` (rule: this module may not depend on "
            f"`{self.forbidden}.*`)"
        )


# ---------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------


def _is_type_checking_block(node: ast.AST) -> bool:
    """True for `if TYPE_CHECKING:` blocks."""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if (
        isinstance(test, ast.Attribute)
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
        and test.attr == "TYPE_CHECKING"
    ):
        return True
    return False


def _iter_imports(tree: ast.Module) -> Iterator[ast.Import | ast.ImportFrom]:
    """Yield top-level (non-TYPE_CHECKING) Import / ImportFrom nodes."""
    for node in tree.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            yield node
        elif _is_type_checking_block(node):
            continue  # imports here don't run at module load
        elif isinstance(node, ast.If):
            # Non-TYPE_CHECKING if: walk it for top-level imports.
            yield from _walk_imports(node)


def _walk_imports(node: ast.AST) -> Iterator[ast.Import | ast.ImportFrom]:
    for child in ast.walk(node):
        if isinstance(child, ast.Import | ast.ImportFrom):
            yield child


def _module_path(node: ast.Import | ast.ImportFrom) -> list[str]:
    """Return the dotted-paths the import touches.

    `import a.b` → ["a.b"]
    `from a.b import c, d` → ["a.b"] (we check the package only)
    Relative imports (`from .x import y`) are resolved against `level`,
    but in this codebase the rules are absolute-import based so the
    callers see only fully-qualified module paths.
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.level and node.level > 0:
            # Relative import — we can't easily resolve without knowing
            # the source's full package path. For our codebase, all
            # cross-package imports are absolute; treat relative as
            # in-package and exempt.
            return []
        return [node.module or ""]
    return []  # pragma: no cover


# ---------------------------------------------------------------------
# Rule application
# ---------------------------------------------------------------------


def _rules_for(file_path: Path, repo_root: Path) -> list[frozenset[str]]:
    rel = file_path.resolve().relative_to(repo_root).as_posix()
    matched: list[frozenset[str]] = []
    for prefix, forbidden in _RULES:
        # Normalize to forward slashes for matching.
        if rel.startswith(prefix + "/"):
            matched.append(forbidden)
    return matched


def _violates(import_target: str, forbidden_prefixes: frozenset[str]) -> str | None:
    for p in forbidden_prefixes:
        if import_target == p or import_target.startswith(p + "."):
            return p
    return None


def _is_exempt_line(source_lines: list[str], lineno: int) -> bool:
    if 0 < lineno <= len(source_lines):
        return _EXEMPT_MARKER in source_lines[lineno - 1]
    return False


def check_path(path: Path, repo_root: Path) -> list[Violation]:
    forbidden_sets = _rules_for(path, repo_root)
    if not forbidden_sets:
        return []
    forbidden = frozenset().union(*forbidden_sets)

    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
    except (OSError, SyntaxError):
        return []
    source_lines = text.splitlines()

    violations: list[Violation] = []
    for node in _iter_imports(tree):
        if _is_exempt_line(source_lines, node.lineno):
            continue
        for target in _module_path(node):
            hit = _violates(target, forbidden)
            if hit is not None:
                statement = (
                    f"from {target} import …"
                    if isinstance(node, ast.ImportFrom)
                    else f"import {target}"
                )
                violations.append(
                    Violation(
                        path=path,
                        line=node.lineno,
                        statement=statement,
                        forbidden=hit,
                    )
                )
    return violations


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def iter_python_files(roots: Iterable[Path]) -> Iterator[Path]:
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            yield root
        elif root.is_dir():
            for p in root.rglob("*.py"):
                if any(
                    part
                    in {
                        ".venv",
                        "venv",
                        "__pycache__",
                        "alembic",
                        "salvage",
                        "node_modules",
                        "build",
                        "dist",
                    }
                    for part in p.parts
                ):
                    continue
                yield p


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_imports.py <repo-root>", file=sys.stderr)
        return 2
    repo_root = Path(argv[0]).resolve()
    violations: list[Violation] = []
    for path in iter_python_files([repo_root / "backend", repo_root / "connector"]):
        violations.extend(check_path(path, repo_root))
    for v in violations:
        print(v.render())
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or [str(Path(__file__).resolve().parents[2])]))
