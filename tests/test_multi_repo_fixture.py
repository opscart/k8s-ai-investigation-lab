import json
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_multi_repo_fixture_requires_cross_repository_context():
    application = (ROOT / "examples/multi-repo/application/service.py").read_text()
    values = (ROOT / "examples/multi-repo/deployment/values.yaml").read_text()
    template = (ROOT / "examples/multi-repo/platform/deployment-template.yaml").read_text()
    assert "BILLING_API_URL" in application
    assert "BILLING_API_URL" not in values
    assert "BILLING_SERVICE_URL" in values
    assert ".Values.service.dependencyEnvName" in template


def test_multi_repo_policies_do_not_expose_evaluation_answers():
    allowed = []
    for role in ("application", "deployment", "platform"):
        policy = json.loads((ROOT / f"config/multi-repo-{role}-policy.json").read_text())
        allowed.extend(policy["allowed_files"])
    assert len(allowed) == 3
    assert all(not path.startswith("evals/") for path in allowed)
