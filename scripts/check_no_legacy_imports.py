"""Fail if production code imports legacy decision modules. See docs/LEGACY.md."""
from __future__ import annotations

import ast
import pathlib
import sys

LEGACY_TAILS = {"engine", "constraints"}
LEGACY_PARENT = "ecofac.decision"
SELF_EXEMPT = {
    pathlib.Path("src/ecofac/decision/engine.py"),
    pathlib.Path("src/ecofac/decision/constraints.py"),
}
ROOTS = ["src", "evaluation", "simulator", "scripts"]


def is_legacy(module: str | None) -> bool:
    if not module:
        return False
    tail = module.split(".")[-1]
    return tail in LEGACY_TAILS and (LEGACY_PARENT in module or "." not in module)


def main() -> int:
    bad: list[tuple[pathlib.Path, int, str]] = []
    for root in ROOTS:
        base = pathlib.Path(root)
        if not base.is_dir():
            continue
        for p in base.rglob("*.py"):
            if p in SELF_EXEMPT:
                continue
            try:
                tree = ast.parse(p.read_text(), filename=str(p))
            except SyntaxError as exc:
                print(f"{p}: cannot parse: {exc}", file=sys.stderr)
                return 1
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and is_legacy(node.module):
                    bad.append((p, node.lineno, node.module or "<relative>"))
                elif isinstance(node, ast.Import):
                    bad.extend(
                        (p, node.lineno, alias.name)
                        for alias in node.names
                        if is_legacy(alias.name)
                    )
    for p, ln, m in bad:
        print(f"{p}:{ln}: imports {m}")
    if bad:
        print("legacy imports detected; see docs/LEGACY.md", file=sys.stderr)
        return 1
    print("no legacy imports in production code")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
