"""Exercise policy against isolated Git histories, not the user's working tree."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "project_check", Path(__file__).resolve().parents[2] / "scripts/project_check.py"
)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def run_git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    run_git(tmp_path, "init")
    run_git(tmp_path, "config", "user.email", "test@example.invalid")
    run_git(tmp_path, "config", "user.name", "Test")
    for name in [
        "AGENTS.md", "README.md", "docs/INDEX.md", "docs/REFERENCES.md",
        "docs/ARCHITECTURE.md", "docs/ROADMAP.md", "docs/RUNBOOK.md",
        "docs/VERIFICATION.md", "docs/WORKFLOW.md", "docs/TESTING.md", "docs/ENGINEERING.md",
    ]:
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# Fixture\n")
    (tmp_path / "docs/INDEX.md").write_text("[component](COMPONENT.md)\n")
    (tmp_path / "docs/COMPONENT.md").write_text("[map](INDEX.md)\n")
    (tmp_path / "source.py").write_text("value = 1\n")
    (tmp_path / "docs/components.json").write_text(json.dumps({"components": [{
        "id": "example", "paths": ["*.py", "docs/components.json", "AGENTS.md", ".agents/**"],
        "doc": "docs/COMPONENT.md", "tests": ["source.py"],
    }]}))
    run_git(tmp_path, "add", ".")
    run_git(tmp_path, "commit", "-m", "baseline")
    return tmp_path


def test_clean_repository_passes(repo):
    assert checker.check(repo, "HEAD") == ([], [])


def test_source_change_requires_component_doc(repo):
    (repo / "source.py").write_text("value = 2\n")
    _, errors = checker.check(repo, "HEAD")
    assert any("update docs/COMPONENT.md" in e for e in errors)
    (repo / "docs/COMPONENT.md").write_text("[map](INDEX.md)\nNew contract\n")
    assert checker.check(repo, "HEAD")[1] == []


def test_document_only_change_does_not_recurse(repo):
    (repo / "README.md").write_text("Updated overview\n")
    assert checker.check(repo, "HEAD")[1] == []


def test_untracked_unmapped_file_is_rejected(repo):
    (repo / "new_service.js").write_text("export {};\n")
    assert any("new_service.js" in e for e in checker.check(repo, "HEAD")[1])


def test_committed_change_compared_to_explicit_base(repo):
    base = run_git(repo, "rev-parse", "HEAD")
    (repo / "source.py").write_text("value = 3\n")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", "missing docs")
    assert any("update docs/COMPONENT.md" in e for e in checker.check(repo, base)[1])


def test_advanced_base_does_not_count_other_branch_changes(repo):
    run_git(repo, "checkout", "-b", "feature")
    base = run_git(repo, "rev-parse", "HEAD")
    run_git(repo, "checkout", "-b", "advanced-main", base)
    (repo / "main_only.js").write_text("export {};\n")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", "unrelated main work")
    run_git(repo, "checkout", "feature")
    assert checker.check(repo, "advanced-main") == ([], [])
    (repo / "source.py").write_text("value = 4\n")
    assert any("update docs/COMPONENT.md" in e for e in checker.check(repo, "advanced-main")[1])


@pytest.mark.parametrize("operation", ["delete", "rename"])
def test_removed_paths_still_require_documentation(repo, operation):
    if operation == "rename":
        (repo / "source.py").rename(repo / "renamed.py")
    else:
        (repo / "source.py").unlink()
    assert any("update docs/COMPONENT.md" in e for e in checker.check(repo, "HEAD")[1])


@pytest.mark.parametrize("target", ["missing.md", "../../outside.md"])
def test_broken_or_escaping_link_rejected(repo, target):
    (repo / "README.md").write_text(f"[bad]({target})\n")
    assert any("broken/outside" in e for e in checker.check(repo, "HEAD")[1])


def test_both_navigation_directions_are_required(repo):
    (repo / "docs/COMPONENT.md").write_text("No map\n")
    assert any("must link docs/INDEX.md" in e for e in checker.check(repo, "HEAD")[1])
    (repo / "docs/COMPONENT.md").write_text("[map](INDEX.md)\n")
    (repo / "docs/INDEX.md").write_text("No component\n")
    assert any("INDEX.md must link" in e for e in checker.check(repo, "HEAD")[1])


def test_invalid_base_fails_instead_of_skipping(repo):
    with pytest.raises(subprocess.CalledProcessError):
        checker.check(repo, "does-not-exist")


@pytest.mark.parametrize("path", ["AGENTS.md", ".agents/skills/example/SKILL.md"])
def test_agent_policy_requires_component_document(repo, path):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("Changed agent policy\n")
    assert any("update docs/COMPONENT.md" in e for e in checker.check(repo, "HEAD")[1])
    (repo / "docs/COMPONENT.md").write_text("[map](INDEX.md)\nChanged policy rationale\n")
    assert checker.check(repo, "HEAD")[1] == []


def test_registry_change_requires_documentation(repo):
    path = repo / "docs/components.json"
    registry = json.loads(path.read_text())
    registry["components"][0]["paths"].append("new/**")
    path.write_text(json.dumps(registry))
    assert any("update docs/COMPONENT.md" in e for e in checker.check(repo, "HEAD")[1])


def test_ambiguous_ownership_fails_even_with_docs(repo):
    path = repo / "docs/components.json"
    registry = json.loads(path.read_text())
    registry["components"].append({
        "id": "overlap", "paths": ["source.py"],
        "doc": "docs/COMPONENT.md", "tests": ["source.py"],
    })
    path.write_text(json.dumps(registry))
    (repo / "docs/COMPONENT.md").write_text("[map](INDEX.md)\nChanged ownership\n")
    assert any("expected one component owner" in e for e in checker.check(repo, "HEAD")[1])


def test_required_knowledge_section_is_checked(repo):
    path = repo / "docs/components.json"
    registry = json.loads(path.read_text())
    registry["required_sections"] = ["Flow"]
    path.write_text(json.dumps(registry))
    (repo / "docs/COMPONENT.md").write_text("[map](INDEX.md)\nUpdated contract\n")
    assert any("missing knowledge section: Flow" in e for e in checker.check(repo, "HEAD")[1])
    (repo / "docs/COMPONENT.md").write_text("[map](INDEX.md)\n## Flow\nInput → result\n")
    assert checker.check(repo, "HEAD")[1] == []
