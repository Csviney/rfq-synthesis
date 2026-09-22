"""POST /ingest: HTTP wiring, failure-to-status-code mapping, and storage.
No network access — a fake extractor (app.state.extractor) stands in for
the real model, per architecture/IMPLEMENTATION_PLAN.md's testing policy.
GET / (the dashboard) is a later step.
"""

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.llm_client import (
    LlmConfigError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.main import app
from app.models import ModelEnvelope

VALID_RFQ = {
    "isRfq": True,
    "confidence": 0.9,
    "customer": {"name": None, "email": None, "phone": None, "company": None},
    "request": {"dueDate": None, "priority": "medium", "specialInstructions": None},
    "lineItems": [
        {
            "partNumber": "LM358N",
            "manufacturer": "TI",
            "description": None,
            "quantity": 500,
            "targetPrice": None,
            "notes": None,
        }
    ],
    "warnings": [],
}

VALID_NON_RFQ = {"isRfq": False, "confidence": 0.95, "reason": "Newsletter, not an RFQ."}

RFQ_RESULT = ModelEnvelope.model_validate({"result": VALID_RFQ}).result
NON_RFQ_RESULT = ModelEnvelope.model_validate({"result": VALID_NON_RFQ}).result


def _raw_email(body: str = "Please quote 500 of LM358N.") -> bytes:
    msg = (
        "From: a@example.com\r\n"
        "Subject: RFQ - test\r\n"
        "Date: Mon, 08 Jun 2026 09:14:00 +0000\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n" + body + "\r\n"
    )
    return msg.encode("utf-8")


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _post(client, raw: bytes, content_type: str = "message/rfc822", **kwargs):
    return client.post("/ingest", content=raw, headers={"content-type": content_type}, **kwargs)


# --- Success paths -------------------------------------------------------


def test_rfq_response_matches_contract_exactly_and_is_stored(client):
    client.app.state.extractor = lambda bundle: RFQ_RESULT

    response = _post(client, _raw_email())

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "isRfq": True,
        "confidence": 0.9,
        "customer": {"name": None, "email": None, "phone": None, "company": None},
        "request": {"dueDate": None, "priority": "medium", "specialInstructions": None},
        "lineItems": [
            {
                "partNumber": "LM358N",
                "manufacturer": "TI",
                "description": None,
                "quantity": 500,
                "targetPrice": None,
                "notes": None,
            }
        ],
        "warnings": [],
    }
    assert "result" not in body  # no provider-envelope wrapper leaks

    stored = client.app.state.store.list()
    assert len(stored) == 1
    assert stored[0].subject == "RFQ - test"
    assert stored[0].result == RFQ_RESULT


def test_non_rfq_response_matches_contract_and_is_not_stored(client):
    client.app.state.extractor = lambda bundle: NON_RFQ_RESULT

    response = _post(client, _raw_email())

    assert response.status_code == 200
    assert response.json() == {
        "isRfq": False,
        "confidence": 0.95,
        "reason": "Newsletter, not an RFQ.",
    }
    assert client.app.state.store.list() == []


def test_reingesting_the_same_email_adds_another_record(client):
    client.app.state.extractor = lambda bundle: RFQ_RESULT
    _post(client, _raw_email())
    _post(client, _raw_email())
    assert len(client.app.state.store.list()) == 2


# --- HTTP-boundary failures (never reach the extractor) ------------------


def test_missing_content_type_is_rejected(client):
    response = client.post("/ingest", content=_raw_email())
    assert response.status_code == 415


def test_wrong_content_type_is_rejected(client):
    response = _post(client, _raw_email(), content_type="application/json")
    assert response.status_code == 415


def test_oversized_body_is_rejected(client):
    """Exercises the fast Content-Length pre-check: TestClient sends a
    normal bytes body, which carries a Content-Length header."""
    original_limit = main_module.MAX_EMAIL_BYTES
    main_module.MAX_EMAIL_BYTES = 10
    try:
        response = _post(client, _raw_email())
    finally:
        main_module.MAX_EMAIL_BYTES = original_limit
    assert response.status_code == 413


def test_oversized_body_without_content_length_is_rejected_while_streaming(client):
    """A generator body has no Content-Length, so this only exercises the
    running-total check over request.stream() — the Content-Length
    pre-check above can't fire here."""
    original_limit = main_module.MAX_EMAIL_BYTES
    main_module.MAX_EMAIL_BYTES = 10

    def body():
        yield b"x" * 33

    try:
        response = client.post(
            "/ingest", content=body(), headers={"content-type": "message/rfc822"}
        )
    finally:
        main_module.MAX_EMAIL_BYTES = original_limit
    assert response.status_code == 413


def test_empty_email_is_rejected_even_when_provider_is_unconfigured(client):
    """Input validation must win over server-side misconfiguration: an
    empty body is invalid regardless of whether a provider is reachable,
    so it must not be masked behind a generic 503."""

    def unconfigured_extractor(bundle):
        raise AssertionError("should not be called for empty source text")

    client.app.state.extractor = unconfigured_extractor
    response = _post(client, b"")
    assert response.status_code == 400


def test_extractor_not_configured_returns_503_for_an_otherwise_valid_email(client):
    def unconfigured_extractor(bundle):
        raise LlmConfigError("missing OPENAI_API_KEY")

    client.app.state.extractor = unconfigured_extractor
    response = _post(client, _raw_email())
    assert response.status_code == 503


# --- Extraction-layer failures: exact exception -> status mapping --------


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (ProviderTimeoutError("timed out"), 504),
        (ProviderUnavailableError("unavailable"), 503),
        (ProviderResponseError("invalid"), 502),
    ],
)
def test_provider_errors_map_to_documented_status_codes(client, exc, status):
    def failing_extractor(bundle):
        raise exc

    client.app.state.extractor = failing_extractor
    response = _post(client, _raw_email())
    assert response.status_code == status
    assert client.app.state.store.list() == []


def test_unsupported_attachment_maps_to_422(client):
    client.app.state.extractor = lambda bundle: RFQ_RESULT

    msg = (
        b"From: a@example.com\r\n"
        b"Subject: RFQ\r\n"
        b"Date: Mon, 08 Jun 2026 09:14:00 +0000\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n'
        b"\r\n"
        b"--B\r\n"
        b"Content-Type: text/plain\r\n\r\n"
        b"See attached.\r\n"
        b"--B\r\n"
        b"Content-Type: image/png\r\n"
        b'Content-Disposition: attachment; filename="scan.png"\r\n\r\n'
        b"not really a png\r\n"
        b"--B--\r\n"
    )
    response = _post(client, msg)
    assert response.status_code == 422


def test_no_usable_source_text_maps_to_422_without_calling_extractor(client):
    def extractor(bundle):
        raise AssertionError("should not be called for empty source text")

    client.app.state.extractor = extractor
    response = _post(client, _raw_email(body=""))
    assert response.status_code == 422


def test_error_responses_never_leak_raw_provider_text(client):
    def failing_extractor(bundle):
        raise ProviderResponseError("SENSITIVE_MARKER_should_not_appear")

    client.app.state.extractor = failing_extractor
    response = _post(client, _raw_email())
    assert "SENSITIVE_MARKER_should_not_appear" not in response.text
