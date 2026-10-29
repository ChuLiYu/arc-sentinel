"""Guard selected CI wiring without depending on a YAML library or GitHub API."""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_ci_jobs_build_and_test_the_same_revision():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    jobs = re.split(r"^  (?:test|docker):\s*$", workflow, flags=re.M)[1:]
    assert len(jobs) == 2
    for job in jobs:
        assert re.search(
            r"uses: actions/checkout@v4\n        with:\n"
            r"          fetch-depth: 0\n"
            r"          ref: \$\{\{ github.event.pull_request.head.sha \|\| github.sha }}",
            job,
        ), "Both jobs must check the same head, with history for base comparisons"


def test_ci_doc_gate_uses_pr_base_or_push_before():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "BASE_SHA: ${{ github.event.pull_request.base.sha || github.event.before }}" in workflow
    assert 'run: python3 scripts/project_check.py --base "$BASE_SHA"' in workflow
    assert "docker info > /dev/null" in workflow
    assert "docker info > /dev/null" in (ROOT / "scripts/verify.sh").read_text()


def test_reviewer_definition_is_parseable_and_scoped():
    config = tomllib.loads((ROOT / ".codex/agents/arc-reviewer.toml").read_text())
    assert config["name"] == "arc-reviewer"
    assert config["description"]
    assert ".agents/skills/arc-review/SKILL.md" in config["developer_instructions"]
    assert "Remain read-only" in config["developer_instructions"]
