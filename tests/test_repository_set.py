import json

import pytest

from investigator.repository_set import RepositoryBinding, RepositorySet


def collection(repo):
    return RepositorySet(
        "default/demo",
        [
            RepositoryBinding("application", repo),
            RepositoryBinding("deployment", repo),
        ],
    )


def test_inventory_and_blobs_are_role_qualified(repo):
    repositories = collection(repo)
    inventory = repositories.inventory()
    assert [item["role"] for item in inventory["repositories"]] == [
        "application",
        "deployment",
    ]
    assert set(repositories.blobs) == {"application:app.py", "deployment:app.py"}


def test_tool_dispatch_requires_repository_role(repo):
    repositories = collection(repo)
    items = repositories.dispatch(
        "read_repository_file",
        json.dumps({"repository": "application", "path": "app.py", "start": 1, "end": 1}),
    )
    assert len(items) == 1
    assert "role=application" in items[0].source
    with pytest.raises(ValueError, match="Unknown repository role"):
        repositories.dispatch(
            "read_repository_file",
            json.dumps({"repository": "unknown", "path": "app.py", "start": 1, "end": 1}),
        )


def test_context_pack_is_deterministic_bounded_and_role_qualified(repo, incident):
    repositories = collection(repo)
    first = repositories.context_pack(incident, max_chars=2000, max_items=2)
    second = repositories.context_pack(incident, max_chars=2000, max_items=2)
    assert first == second
    assert len(first) == 2
    assert sum(len(item.text) for item in first) <= 2000
    assert {item.source.split(";", 1)[0] for item in first} == {
        "role=application",
        "role=deployment",
    }
    assert "EVALUATION-ANSWER" not in json.dumps([item.model_dump() for item in first])
