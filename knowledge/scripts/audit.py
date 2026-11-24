#!/usr/bin/env python3
"""Non-destructive consistency audit for the GeoDE knowledge base.

Checks the knowledge/ docs against the actual repository state and prints a markdown report.
Never modifies anything. Exit code is 0 even when findings are reported (findings are not a
pytest-style failure) -- this is a report-generation tool, not a CI gate.

Usage: python knowledge/scripts/audit.py [--repo-root PATH]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / ".git").exists():
            return candidate
    return start.resolve()


class Report:
    def __init__(self) -> None:
        self.ok: list[str] = []
        self.warnings: list[str] = []

    def add_ok(self, msg: str) -> None:
        self.ok.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def render(self) -> str:
        lines = ["# GeoDE Knowledge Base Audit", ""]
        lines.append(f"**{len(self.warnings)} warning(s), {len(self.ok)} check(s) passed.**")
        lines.append("")
        if self.warnings:
            lines.append("## Warnings")
            lines.append("")
            for w in self.warnings:
                lines.append(f"- {w}")
            lines.append("")
        lines.append("## Passed checks")
        lines.append("")
        for o in self.ok:
            lines.append(f"- {o}")
        return "\n".join(lines) + "\n"


_FILE_EXTENSIONS = r"py|sql|md|cfg|json|toml|ya?ml|sh|txt|rst|csv|jar|png|jpg|cff|in"
PATH_PATTERN = re.compile(
    r"(?<![\w./])((?:geode|com|database|configuration_files|scripts)/"
    r"[A-Za-z0-9_./-]+\.(?:" + _FILE_EXTENSIONS + r"))\b"
)


def check_referenced_paths(root: Path, report: Report) -> None:
    """Every file-looking path mentioned in knowledge/**/*.md should actually exist."""
    knowledge_dir = root / "knowledge"
    checked = 0
    missing: list[tuple[str, str]] = []
    for md_file in sorted(knowledge_dir.rglob("*.md")):
        text = md_file.read_text(encoding="utf-8", errors="replace")
        for match in PATH_PATTERN.finditer(text):
            rel = match.group(1)
            checked += 1
            if not (root / rel).exists():
                missing.append((str(md_file.relative_to(root)), rel))
    if missing:
        for doc, rel in missing:
            report.add_warning(f"`{doc}` references `{rel}`, which does not exist in the repo.")
    else:
        report.add_ok(f"All {checked} file-path references found in knowledge/**/*.md exist on disk.")


def check_sphinx_automodule_targets(root: Path, report: Report) -> None:
    """Every `.. automodule:: geode.X` / `com.X` target in docs/*.rst should resolve to a real module."""
    docs_dir = root / "docs"
    if not docs_dir.exists():
        return
    automodule_re = re.compile(r"automodule::\s*([A-Za-z0-9_.-]+)")
    missing = []
    checked = 0
    for rst_file in sorted(docs_dir.glob("*.rst")):
        text = rst_file.read_text(encoding="utf-8", errors="replace")
        for match in automodule_re.finditer(text):
            dotted = match.group(1)
            checked += 1
            parts = dotted.split(".")
            as_module = root.joinpath(*parts[:-1], parts[-1] + ".py")
            as_package = root.joinpath(*parts, "__init__.py")
            if not as_module.exists() and not as_package.exists():
                missing.append((str(rst_file.relative_to(root)), dotted))
    if missing:
        for doc, dotted in missing:
            report.add_warning(
                f"`{doc}` has `.. automodule:: {dotted}`, but no `{dotted.replace('.', '/')}.py` "
                f"or package `__init__.py` exists — Sphinx autodoc target is stale."
            )
    elif checked:
        report.add_ok(f"All {checked} Sphinx `automodule` targets in docs/*.rst resolve to real modules.")


def check_setup_py_scripts_documented(root: Path, report: Report) -> None:
    """Every script in setup.py's scripts=[...] list should appear in the CLI reference map."""
    setup_py = root / "setup.py"
    cli_map = root / "knowledge" / "architecture" / "cli-reference-map.md"
    if not setup_py.exists() or not cli_map.exists():
        return
    setup_text = setup_py.read_text(encoding="utf-8", errors="replace")
    scripts = re.findall(r"'(com/[A-Za-z0-9_.-]+\.py)'", setup_text)
    com_scripts = {Path(s).name for s in scripts}
    map_text = cli_map.read_text(encoding="utf-8", errors="replace")
    undocumented = sorted(name for name in com_scripts if name not in map_text)
    if undocumented:
        report.add_warning(
            "Scripts installed via setup.py but not mentioned in "
            f"knowledge/architecture/cli-reference-map.md: {', '.join(undocumented)}"
        )
    else:
        report.add_ok(
            f"All {len(com_scripts)} CLI scripts in setup.py's scripts=[...] are mentioned in "
            "cli-reference-map.md."
        )

    # Reverse check: scripts named in the map's first (Script) table column that no longer
    # exist under com/. Only the first column counts -- other columns legitimately reference
    # library modules (e.g. geode/pyStack.py) that aren't com/ scripts at all.
    first_col_scripts: set[str] = set()
    for line in map_text.splitlines():
        if not line.startswith("| `"):
            continue
        first_cell = line.split("|")[1]
        first_col_scripts.update(re.findall(r"`([A-Za-z0-9_.-]+\.py)`", first_cell))
    stale = sorted(name for name in first_col_scripts if not (root / "com" / name).exists())
    if stale:
        report.add_warning(
            "Scripts named in cli-reference-map.md's Script column that no longer exist under "
            f"com/: {', '.join(stale)}"
        )


def check_readme_remote_consistency(root: Path, report: Report) -> None:
    """README.md's GitHub links should point at the actual git remote, not a renamed/old repo."""
    readme = root / "README.md"
    if not readme.exists():
        return
    try:
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root, capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return
    remote_slug_match = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$", remote)
    if not remote_slug_match:
        return
    remote_slug = remote_slug_match.group(1)
    owner = remote_slug.split("/")[0]
    text = readme.read_text(encoding="utf-8", errors="replace")
    other_repos = set(re.findall(r"github\.com/([\w.-]+/[\w.-]+)", text)) - {remote_slug}
    # Only flag same-owner repos (a different owner is normally a genuine external dependency,
    # e.g. a forked PPP engine -- not a stale self-reference) whose name suggests an old name for
    # this same project rather than an unrelated tool.
    same_owner_other_names = sorted(r for r in other_repos if r.startswith(f"{owner}/"))
    if same_owner_other_names:
        report.add_warning(
            f"README.md references other {owner}/* GitHub repo(s) {same_owner_other_names} "
            f"alongside the actual origin remote ({remote_slug}) — verify these aren't a stale "
            "self-reference to an old project name rather than a genuine separate repo."
        )
    else:
        report.add_ok(f"README.md's self-referential GitHub links are consistent with the origin remote ({remote_slug}).")


def check_egg_info_cruft(root: Path, report: Report) -> None:
    stale_dirs = [p for p in root.glob("*.egg-info") if p.is_dir()]
    if len(stale_dirs) > 1:
        names = ", ".join(p.name for p in stale_dirs)
        report.add_warning(
            f"Multiple *.egg-info directories present ({names}) — likely a stale build artifact "
            "from before a package rename; see knowledge/known-issues.md."
        )


def check_knowledge_internal_links(root: Path, report: Report) -> None:
    """Relative markdown links within knowledge/**/*.md should resolve."""
    knowledge_dir = root / "knowledge"
    link_re = re.compile(r"\]\(((?:\.\./|\./|[A-Za-z0-9_-])[^)\s]+\.md)[^)]*\)")
    broken = []
    checked = 0
    for md_file in sorted(knowledge_dir.rglob("*.md")):
        for match in link_re.finditer(md_file.read_text(encoding="utf-8", errors="replace")):
            target = (md_file.parent / match.group(1)).resolve()
            checked += 1
            if not target.exists():
                broken.append((str(md_file.relative_to(root)), match.group(1)))
    if broken:
        for doc, target in broken:
            report.add_warning(f"`{doc}` has a broken relative link to `{target}`.")
    elif checked:
        report.add_ok(f"All {checked} relative markdown links within knowledge/ resolve.")


CHECKS = [
    check_referenced_paths,
    check_sphinx_automodule_targets,
    check_setup_py_scripts_documented,
    check_readme_remote_consistency,
    check_egg_info_cruft,
    check_knowledge_internal_links,
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args()

    root = args.repo_root or find_repo_root(Path(__file__).parent)
    report = Report()
    for check in CHECKS:
        check(root, report)

    print(report.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
