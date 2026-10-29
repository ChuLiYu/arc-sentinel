#!/usr/bin/env python3
"""Dependency-free component ownership, document co-change and local-link gate."""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit


def git(root: Path, *args: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True
    )
    return [p.decode() for p in result.stdout.split(b"\0") if p]


def local_links(root: Path, document: str) -> list[Path]:
    # Repository navigation uses inline Markdown links without spaces in targets.
    content = (root / document).read_text()
    content = re.sub(r"```.*?```", "", content, flags=re.S)
    targets = re.findall(r"(?<!!)\[[^\]]*\]\(([^\s)]+)\)", content)
    result = []
    for target in targets:
        parsed = urlsplit(target.strip("<>"))
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        result.append(((root / document).parent / unquote(parsed.path)).resolve())
    return result


def check(root: Path, base: str) -> tuple[list[str], list[str]]:
    # Ref validation is mandatory: an incomplete/shallow checkout must not pass.
    subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", f"{base}^{{commit}}"],
        check=True, capture_output=True,
    )
    registry = json.loads((root / "docs/components.json").read_text())
    components = registry["components"]
    required_sections = registry.get("required_sections", [])
    # PR base may advance independently: compare this branch's changes since
    # divergence, not unrelated work added to main after divergence.
    common = subprocess.run(
        ["git", "-C", str(root), "merge-base", base, "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    changed = set(git(root, "diff", "--no-renames", "--name-only", "-z", common, "--"))
    untracked = set(git(root, "ls-files", "--others", "--exclude-standard", "-z"))
    changed |= untracked
    tracked = set(git(root, "ls-files", "-z")) | untracked
    errors, affected = [], []
    documents = {
        "AGENTS.md", "README.md", "docs/INDEX.md", "docs/REFERENCES.md",
        "docs/ARCHITECTURE.md", "docs/ROADMAP.md", "docs/RUNBOOK.md",
        "docs/VERIFICATION.md", "docs/WORKFLOW.md", "docs/TESTING.md", "docs/ENGINEERING.md",
    }
    documents |= {p for p in tracked if p.startswith(".agents/") and p.endswith(".md")}
    # Markdown instructions are executable policy inputs, not ordinary prose.
    def policy(path: str) -> bool:
        return path == "AGENTS.md" or path.startswith(".agents/")

    seen = set()
    for component in components:
        name, doc = component["id"], component["doc"]
        if name in seen:
            errors.append(f"duplicate component: {name}")
        seen.add(name)
        documents.add(doc)
        owned = {
            p for p in changed if (not p.lower().endswith(".md") or policy(p))
            and any(fnmatch.fnmatchcase(p, pattern) for pattern in component["paths"])
        }
        if owned:
            affected.append(f"{name}: {doc} ({len(owned)} changed files)")
            if doc not in changed:
                errors.append(f"{name}: update {doc} alongside: {', '.join(sorted(owned))}")
        if not (root / doc).is_file():
            errors.append(f"missing component document: {doc}")
        else:
            content = (root / doc).read_text()
            headings = re.findall(r"^## (.+)$", content, flags=re.M)
            for section in required_sections:
                if section not in headings:
                    errors.append(f"{doc}: missing knowledge section: {section}")
        for test in component["tests"]:
            if not (root / test).exists():
                errors.append(f"{name}: missing test entry {test}")
    # Cover deleted paths as well, and reject ambiguous ownership.
    for path in sorted(tracked | changed):
        if (path.lower().endswith(".md") and not policy(path)) or path == "LICENSE":
            continue
        owners = [c["id"] for c in components if any(
            fnmatch.fnmatchcase(path, pattern) for pattern in c["paths"]
        )]
        if len(owners) != 1:
            errors.append(f"{path}: expected one component owner, found {owners}")
    # Registry is a policy artifact, owned by automation even though it lives in docs.
    for doc in sorted(documents):
        if not (root / doc).is_file():
            errors.append(f"missing managed document: {doc}")
            continue
        for target in local_links(root, doc):
            if not target.is_relative_to(root.resolve()) or not target.exists():
                errors.append(f"{doc}: broken/outside local link: {target}")
    index = root / "docs/INDEX.md"
    if index.is_file():
        forward = local_links(root, "docs/INDEX.md")
        for component in components:
            doc = component["doc"]
            if (root / doc).is_file():
                if (root / doc).resolve() not in forward:
                    errors.append(f"docs/INDEX.md must link {doc}")
                if index.resolve() not in local_links(root, doc):
                    errors.append(f"{doc} must link docs/INDEX.md")
    return affected, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="HEAD", help="PR base ref; HEAD for working changes")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        affected, errors = check(root, args.base)
    except (subprocess.CalledProcessError, OSError, ValueError, KeyError) as exc:
        print(f"project check could not complete: {exc}")
        return 1
    print("Affected components:\n" + "\n".join(affected or ["none"]))
    if errors:
        print("\n".join(f"ERROR: {e}" for e in errors))
        return 1
    print("Project mapping, document co-change and local links OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
