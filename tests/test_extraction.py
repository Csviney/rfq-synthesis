"""Offline tests for the prompt/call wrapper (app/llm_client.extract) and
the parse -> extract -> finalize orchestration (app/rfq_service). No
network access: a fake OpenAI client and a fake extractor stand in for the
real model. See tests/test_live_samples.py for the required real-model
checks (a body RFQ and a non-RFQ working end to end).
"""

import json
from types import SimpleNamespace

import httpx
import openai
import pydantic
import pytest

from app.llm_client import (
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    extract,
)
from app.models import ModelEnvelope, NoUsableSourceTextError, RfqResult, SourceBundle, SourceChunk
from app.rfq_service import process_email

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

_FAKE_REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


class _FakeCompletions:
    def __init__(self, behavior):
        self._behavior = behavior
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self._behavior(**kwargs)


class _FakeClient:
    def __init__(self, behavior):
        completions = _FakeCompletions(behavior)
        self.beta = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        self.completions = completions  # convenience for assertions


def _completion_with(*, parsed=None, refusal=None):
    message = SimpleNamespace(parsed=parsed, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


BUNDLE = SourceBundle(
    chunks=[
        SourceChunk(name="email headers", text="From: a@example.com\nSubject: RFQ"),
        SourceChunk(name="email body", text="Please quote 500 of LM358N."),
    ],
    warnings=[],
)


def test_extract_returns_result_on_success():
    envelope = ModelEnvelope.model_validate({"result": VALID_RFQ})
    client = _FakeClient(lambda **kwargs: _completion_with(parsed=envelope))

    result = extract(client, "gpt-4o-mini", BUNDLE)

    assert isinstance(result, RfqResult)
    assert result.lineItems[0].partNumber == "LM358N"


def test_extract_sends_instructions_and_source_as_separate_messages():
    envelope = ModelEnvelope.model_validate({"result": VALID_NON_RFQ})
    client = _FakeClient(lambda **kwargs: _completion_with(parsed=envelope))

    extract(client, "gpt-4o-mini", BUNDLE)

    [call] = client.completions.calls
    assert call["response_format"] is ModelEnvelope
    system_msg, user_msg = call["messages"]
    assert system_msg["role"] == "system"
    assert user_msg["role"] == "user"
    # The source bundle must be readable in the user message as data...
    payload = json.loads(user_msg["content"])
    assert payload["source"][1]["text"] == "Please quote 500 of LM358N."
    # ...and not appear inside the trusted instructions.
    assert "LM358N" not in system_msg["content"]


def test_extract_raises_on_refusal():
    client = _FakeClient(lambda **kwargs: _completion_with(refusal="cannot help with that"))
    with pytest.raises(ProviderResponseError):
        extract(client, "gpt-4o-mini", BUNDLE)


def test_extract_raises_when_no_parsed_result():
    client = _FakeClient(lambda **kwargs: _completion_with(parsed=None))
    with pytest.raises(ProviderResponseError):
        extract(client, "gpt-4o-mini", BUNDLE)


def test_extract_raises_provider_timeout_error():
    def behavior(**kwargs):
        raise openai.APITimeoutError(request=_FAKE_REQUEST)

    client = _FakeClient(behavior)
    with pytest.raises(ProviderTimeoutError):
        extract(client, "gpt-4o-mini", BUNDLE)


def test_extract_raises_provider_unavailable_on_connection_error():
    def behavior(**kwargs):
        raise openai.APIConnectionError(request=_FAKE_REQUEST)

    client = _FakeClient(behavior)
    with pytest.raises(ProviderUnavailableError):
        extract(client, "gpt-4o-mini", BUNDLE)


def test_extract_raises_provider_response_error_on_length_finish_reason():
    def behavior(**kwargs):
        raise openai.LengthFinishReasonError(completion=SimpleNamespace(usage=None))

    client = _FakeClient(behavior)
    with pytest.raises(ProviderResponseError):
        extract(client, "gpt-4o-mini", BUNDLE)


def test_extract_raises_provider_response_error_on_content_filter():
    def behavior(**kwargs):
        raise openai.ContentFilterFinishReasonError()

    client = _FakeClient(behavior)
    with pytest.raises(ProviderResponseError):
        extract(client, "gpt-4o-mini", BUNDLE)


def test_extract_raises_provider_response_error_on_locally_invalid_result():
    """Structured output constrains the JSON *shape*, not every local
    constraint layered on top of it in app/models.py — e.g. our own
    non-blank partNumber check. The real SDK validates the provider's JSON
    against ModelEnvelope inside .parse() and lets a resulting
    pydantic.ValidationError escape uncaught; simulate that here with a
    real ValidationError rather than a stand-in exception."""
    bad_result = {**VALID_RFQ, "lineItems": [{**VALID_RFQ["lineItems"][0], "partNumber": "   "}]}
    try:
        ModelEnvelope.model_validate({"result": bad_result})
        pytest.fail("expected ModelEnvelope validation to reject a blank partNumber")
    except pydantic.ValidationError as validation_error:
        real_error = validation_error

    def behavior(**kwargs):
        raise real_error

    client = _FakeClient(behavior)
    with pytest.raises(ProviderResponseError):
        extract(client, "gpt-4o-mini", BUNDLE)


# --- rfq_service.process_email: fake extractor, no network at all -------


def _raw_email(body: str = "Please quote 500 of LM358N.") -> bytes:
    msg = (
        "From: a@example.com\r\n"
        "Subject: RFQ\r\n"
        "Date: Mon, 08 Jun 2026 09:14:00 +0000\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n" + body + "\r\n"
    )
    return msg.encode("utf-8")


def test_process_email_passes_non_rfq_through_unchanged():
    non_rfq = ModelEnvelope.model_validate({"result": VALID_NON_RFQ}).result
    result = process_email(_raw_email(), lambda bundle: non_rfq)
    assert result is non_rfq


def test_process_email_merges_parser_warnings_into_rfq_warnings():
    rfq = ModelEnvelope.model_validate({"result": VALID_RFQ}).result
    # An HTML-only body forces a parser warning ("Body was HTML-only...").
    html_email = (
        b"From: a@example.com\r\n"
        b"Subject: RFQ\r\n"
        b"Date: Mon, 08 Jun 2026 09:14:00 +0000\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<p>Please quote 500 of LM358N.</p>\r\n"
    )
    result = process_email(html_email, lambda bundle: rfq)
    assert any("HTML" in w for w in result.warnings)


def test_process_email_flags_due_date_before_email_date_without_changing_it():
    rfq_with_early_due_date = RfqResult.model_validate(
        {**VALID_RFQ, "request": {**VALID_RFQ["request"], "dueDate": "2020-01-01"}}
    )
    result = process_email(_raw_email(), lambda bundle: rfq_with_early_due_date)

    assert result.request.dueDate.isoformat() == "2020-01-01"  # unchanged
    assert any("before the email date" in w for w in result.warnings)


def test_process_email_flags_rfq_with_no_line_items():
    empty_rfq = RfqResult.model_validate({**VALID_RFQ, "lineItems": []})
    result = process_email(_raw_email(), lambda bundle: empty_rfq)
    assert any("no identifiable line items" in w for w in result.warnings)


def test_process_email_rejects_empty_source_without_calling_extractor():
    calls = []

    def extractor(bundle):
        calls.append(bundle)
        raise AssertionError("extractor should not be called for empty source text")

    with pytest.raises(NoUsableSourceTextError):
        process_email(_raw_email(body=""), extractor)
    assert calls == []
