"""GET / (the dashboard): empty state, that a newly ingested RFQ appears
after refresh with all fields/warnings/triage, that missing/zero values
and untrusted text render safely, and the triage-category filter.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import ModelEnvelope

VALID_RFQ = {
    "isRfq": True,
    "confidence": 0.9,
    "customer": {
        "name": "Jane Buyer",
        "email": "jane@example.com",
        "phone": None,
        "company": "Acme Electronics",
    },
    "request": {"dueDate": "2026-06-01", "priority": "high", "specialInstructions": None},
    "lineItems": [
        {
            "partNumber": "LM358N",
            "manufacturer": "TI",
            "description": None,
            "quantity": 500,
            "targetPrice": 1.1,
            "notes": None,
        },
        {
            "partNumber": "UNKNOWN-QTY-PART",
            "manufacturer": None,
            "description": None,
            "quantity": 0,
            "targetPrice": None,
            "notes": None,
        },
    ],
    "warnings": ["Quantity not stated for UNKNOWN-QTY-PART; defaulted to 0."],
}

# The model's own guess. VALID_RFQ has an unspecified (zero) quantity, so
# rfq_service._reconcile_triage overrides this to clarify_with_customer
# regardless of what's supplied here — several tests below rely on that.
VALID_TRIAGE = {"category": "begin_pricing", "reason": "looks fine", "nextStep": "quote it"}

RFQ_ENVELOPE = ModelEnvelope.model_validate({"result": VALID_RFQ, "triage": VALID_TRIAGE})
RFQ_RESULT = RFQ_ENVELOPE.result

# A second, fully-specified RFQ with no deterministic-override triggers, so
# its triage passes through as the model stated it. Same customer priority
# as VALID_RFQ ("high") but a different requirement shape, so the two
# demonstrate different recommended actions despite equal priority.
CLEAN_RFQ = {
    "isRfq": True,
    "confidence": 0.92,
    "customer": {
        "name": "Sam Sourcing",
        "email": "sam@example.com",
        "phone": None,
        "company": "Northwind Controls",
    },
    "request": {
        "dueDate": None,
        "priority": "high",
        "specialInstructions": "Delivery must be coordinated across two sites.",
    },
    "lineItems": [
        {
            "partNumber": "STM32F407VGT6",
            "manufacturer": "ST",
            "description": None,
            "quantity": 300,
            "targetPrice": None,
            "notes": None,
        }
    ],
    "warnings": [],
}
CLEAN_TRIAGE = {
    "category": "review_sourcing",
    "reason": "Delivery must be coordinated across two sites before pricing.",
    "nextStep": "Confirm split-delivery logistics with sourcing.",
}
CLEAN_ENVELOPE = ModelEnvelope.model_validate({"result": CLEAN_RFQ, "triage": CLEAN_TRIAGE})


def _raw_email(subject: str = "RFQ - test", body: str = "Please quote 500 of LM358N.") -> bytes:
    msg = (
        f"From: a@example.com\r\n"
        f"Subject: {subject}\r\n"
        "Date: Mon, 08 Jun 2026 09:14:00 +0000\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n" + body + "\r\n"
    )
    return msg.encode("utf-8")


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _ingest(client, envelope, subject: str = "RFQ - test"):
    client.app.state.extractor = lambda bundle: envelope
    response = client.post(
        "/ingest", content=_raw_email(subject=subject), headers={"content-type": "message/rfc822"}
    )
    assert response.status_code == 200


def test_empty_state(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "No RFQs ingested yet" in response.text


def test_ingested_rfq_appears_after_refresh_with_all_fields_and_warnings(client):
    _ingest(client, RFQ_ENVELOPE)
    page = client.get("/").text

    # Customer / request
    assert "Jane Buyer" in page
    assert "Acme Electronics" in page
    assert "2026-06-01" in page
    assert "high" in page

    # Both line items, including the zero-quantity one
    assert "LM358N" in page
    assert "UNKNOWN-QTY-PART" in page
    assert ">500<" in page
    assert ">0<" in page  # quantity 0 must render as "0", not blank or "—"

    # A missing (null) value renders the placeholder, not blank/"None"
    assert "—" in page
    assert ">None<" not in page

    # The warning is shown
    assert "Quantity not stated for UNKNOWN-QTY-PART" in page

    # Confidence shown as a percentage
    assert "90%" in page


def test_non_rfq_is_not_shown_on_dashboard(client):
    non_rfq_envelope = ModelEnvelope.model_validate(
        {"result": {"isRfq": False, "confidence": 0.9, "reason": "Newsletter."}, "triage": None}
    )
    _ingest(client, non_rfq_envelope)
    page = client.get("/").text
    assert "No RFQs ingested yet" in page


def test_newest_rfq_appears_first(client):
    _ingest(client, RFQ_ENVELOPE, subject="First RFQ")
    _ingest(client, RFQ_ENVELOPE, subject="Second RFQ")

    page = client.get("/").text
    assert page.index("Second RFQ") < page.index("First RFQ")


def test_untrusted_text_is_escaped_not_executed(client):
    # No zero-quantity item here, and dueDate is consistent — nothing
    # triggers a deterministic triage override, so this malicious triage
    # text actually reaches the page and can be checked for escaping.
    malicious_result = {
        **CLEAN_RFQ,
        "warnings": ['<script>document.title="pwned"</script>'],
        "customer": {**CLEAN_RFQ["customer"], "name": "<img src=x onerror=alert(1)>"},
    }
    malicious_triage = {
        "category": "review_sourcing",
        "reason": '<script>alert("triage")</script>',
        "nextStep": "<img src=y onerror=alert(2)>",
    }
    malicious = ModelEnvelope.model_validate({"result": malicious_result, "triage": malicious_triage})
    _ingest(client, malicious)

    page = client.get("/").text
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert "<img src=x" not in page
    assert "<img src=y" not in page
    assert "&lt;img" in page


def test_recommended_action_badge_reason_and_next_step_are_displayed(client):
    _ingest(client, CLEAN_ENVELOPE)
    page = client.get("/").text

    assert "Review sourcing requirements" in page
    assert "Delivery must be coordinated across two sites before pricing." in page
    assert "Confirm split-delivery logistics with sourcing." in page
    assert "Advisory" in page  # recommendation is clearly marked non-authoritative


def test_deterministic_reconciliation_overrides_model_triage_for_unspecified_quantity(client):
    """RFQ_ENVELOPE's model guess was "begin_pricing"; VALID_RFQ has an
    unspecified quantity, so the displayed recommendation must be the
    service's override, not the model's original guess."""
    _ingest(client, RFQ_ENVELOPE)
    page = client.get("/").text

    # "Begin pricing" legitimately appears in the filter nav regardless of
    # what's displayed, so check the actual badge class, not the raw text.
    assert 'triage-clarify_with_customer' in page
    assert 'triage-begin_pricing"' not in page
    assert "UNKNOWN-QTY-PART" in page  # the override names the affected part


def test_customer_priority_is_labeled_distinctly_from_recommended_action(client):
    _ingest(client, RFQ_ENVELOPE)
    page = client.get("/").text

    assert "Customer priority" in page
    assert "Recommended action" in page
    # Not a claim that *this* value was customer-stated — priority
    # defaults to medium when unstated, so the note must stay accurate
    # for both cases rather than asserting this one was customer-given.
    assert "default when urgency isn't stated" in page


def test_filter_shows_only_matching_category_and_marks_it_active(client):
    _ingest(client, RFQ_ENVELOPE, subject="Needs clarification")
    _ingest(client, CLEAN_ENVELOPE, subject="Needs sourcing review")

    response = client.get("/", params={"category": "review_sourcing"})
    page = response.text

    assert "Needs sourcing review" in page
    assert "Needs clarification" not in page
    assert "1 of 2" in page
    assert 'href="/?category=review_sourcing" class="active"' in page


def test_filter_with_no_matches_shows_empty_message_not_the_no_rfqs_message(client):
    _ingest(client, CLEAN_ENVELOPE)  # only a review_sourcing RFQ exists

    page = client.get("/", params={"category": "begin_pricing"}).text
    assert "No RFQs match this filter" in page
    assert "No RFQs ingested yet" not in page


def test_all_rfqs_filter_shows_everything_with_accurate_count(client):
    _ingest(client, RFQ_ENVELOPE, subject="Needs clarification")
    _ingest(client, CLEAN_ENVELOPE, subject="Needs sourcing review")

    page = client.get("/").text
    assert "Needs clarification" in page
    assert "Needs sourcing review" in page
    assert "2 RFQs ingested this session" in page


def test_invalid_category_query_param_is_rejected(client):
    response = client.get("/", params={"category": "not_a_real_category"})
    assert response.status_code == 422


def test_same_customer_priority_can_yield_different_recommended_actions(client):
    """Both fixtures state priority "high"; triage must not just mirror it."""
    assert VALID_RFQ["request"]["priority"] == CLEAN_RFQ["request"]["priority"] == "high"

    _ingest(client, RFQ_ENVELOPE, subject="Needs clarification")
    _ingest(client, CLEAN_ENVELOPE, subject="Needs sourcing review")

    page = client.get("/").text
    assert "Clarify with customer" in page
    assert "Review sourcing requirements" in page
