from types import SimpleNamespace

from investigator.diagnostics import failure_details


def test_default_does_not_expose_error_body():
    exc = RuntimeError("SECRET")
    exc.status_code = 400
    exc.body = {"error": {"message": "SECRET", "code": "bad_input"}}
    assert failure_details(exc, "agent_creation", False) == {
        "stage": "agent_creation",
        "exception": "RuntimeError",
        "http_status": 400,
    }


def test_debug_selects_bounded_fields_only():
    exc = RuntimeError("unselected exception")
    exc.body = {"error": {"message": "x" * 3000, "param": "tools", "headers": "SECRET"}}
    result = failure_details(exc, "model_request", True)
    assert len(result["provider_error"]["message"]) == 2000
    assert result["provider_error"]["param"] == "tools"
    assert "SECRET" not in str(result)
    assert "unselected" not in str(result)


def test_debug_supports_azure_parsed_errors():
    exc = RuntimeError()
    exc.error = SimpleNamespace(code="BadRequest", message="Unsupported parameter")
    assert failure_details(exc, "agent_creation", True)["provider_error"] == {
        "code": "BadRequest",
        "message": "Unsupported parameter",
    }
