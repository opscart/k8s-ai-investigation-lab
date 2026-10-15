import pytest

from investigator.sanitization import (
    contains_kubernetes_secret_manifest,
    repository_path_allowed,
    sanitize_provider_text,
)


@pytest.mark.parametrize(
    ("kind", "secret"),
    [
        ("bearer_token", "Authorization: Bearer token-value-that-must-not-leak"),
        ("authorization_header", "Authorization: Basic dXNlcjpwYXNzd29yZA=="),
        ("jwt", "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoiYWRtaW4ifQ.abc123signature"),
        ("known_token", "github_pat_0123456789abcdefghijklmnop"),
        ("known_token", "sk-proj-abcdef1234567890ABCDEF1234567890"),
        ("known_token", "AKIAABCDEFGHIJKLMNOP"),
        ("basic_auth_url", "https://user:password-value@example.invalid/path"),
        ("sensitive_assignment", "password=super-secret-value"),
        ("sensitive_json", '"password": "json-secret-value"'),
        ("sensitive_assignment", "sig=azure-sas-signature"),
        ("sensitive_xml", "<password>xml-secret-value</password>"),
        (
            "private_key",
            "-----BEGIN PRIVATE KEY-----\nsynthetic-private-material\n-----END PRIVATE KEY-----",
        ),
    ],
)
def test_sanitize_provider_text_removes_adversarial_credentials(kind, secret):
    result = sanitize_provider_text(f"before {secret} after")
    assert secret not in result.text
    assert "[REDACTED:" in result.text
    assert result.redactions[kind] == 1


def test_sanitize_provider_text_preserves_normal_error_identifiers():
    text = "ExampleErrorSink ErrorHandlerConfiguration error-channel sharedSecretName"
    result = sanitize_provider_text(text)
    assert result.text == text
    assert result.redactions == {}


def test_private_key_redaction_preserves_source_line_numbers():
    text = "before\n-----BEGIN PRIVATE KEY-----\nmaterial\n-----END PRIVATE KEY-----\nafter"
    result = sanitize_provider_text(text)
    assert result.text.splitlines()[-1] == "after"
    assert len(result.text.splitlines()) == len(text.splitlines())


@pytest.mark.parametrize(
    "path",
    [".env", ".env.production", "certs/client.key", "certs/client.pem", "app/config.pfx"],
)
def test_repository_path_policy_rejects_credential_bearing_files(path):
    assert not repository_path_allowed(path)


def test_repository_path_policy_allows_source_and_pipeline_files():
    assert repository_path_allowed("src/main/java/SecretConfig.java")
    assert repository_path_allowed("templates/jobs/maven-test.yml")


def test_kubernetes_secret_manifest_is_detected_before_provider_context():
    assert contains_kubernetes_secret_manifest("apiVersion: v1\nkind: Secret\ndata:\n  key: value")
    assert not contains_kubernetes_secret_manifest("apiVersion: v1\nkind: Deployment")
