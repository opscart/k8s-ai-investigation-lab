import importlib.util
from pathlib import Path

import pytest

from investigator.contracts import Incident, RepositoryPolicy

ROOT = Path(__file__).parents[1]


def load_fixture_app():
    path = ROOT / "examples/config-mismatch/app.py"
    spec = importlib.util.spec_from_file_location("config_mismatch_app", path)
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    return app


def test_configuration_fixture_requires_name_missing_from_deployment():
    app = load_fixture_app()
    deployment = (ROOT / "examples/config-mismatch/deployment.yaml").read_text()

    assert app.REQUIRED_BACKEND_VARIABLE == "INVENTORY_API_URL"
    assert "name: INVENTORY_SERVICE_URL" in deployment
    assert "name: INVENTORY_API_URL" not in deployment
    with pytest.raises(ValueError, match="startup configuration invalid"):
        app.load_backend_url({"INVENTORY_SERVICE_URL": "http://inventory-api:8080"})


def test_configuration_fixture_accepts_expected_variable():
    app = load_fixture_app()
    assert (
        app.load_backend_url({"INVENTORY_API_URL": "http://inventory-api:8080"})
        == "http://inventory-api:8080"
    )


def test_configuration_evaluation_inputs_are_valid_and_answers_stay_outside_policy():
    incident = Incident.model_validate_json((ROOT / "evals/cases/config-mismatch.json").read_text())
    policy = RepositoryPolicy.model_validate_json(
        (ROOT / "config/config-mismatch-policy.json").read_text()
    )

    assert incident.case_id == "config-001"
    assert len({item.id for item in incident.evidence}) == len(incident.evidence)
    assert all(not path.startswith("evals/expected/") for path in policy.allowed_files)
    assert set(policy.allowed_files) == {
        "examples/config-mismatch/app.py",
        "examples/config-mismatch/deployment.yaml",
        "examples/config-mismatch/runbook.md",
    }
