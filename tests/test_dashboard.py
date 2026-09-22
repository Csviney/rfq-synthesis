"""GET / (the dashboard): empty state, that a newly ingested RFQ appears
after refresh with all fields and warnings, and that missing/zero values
and untrusted text render safely. See architecture/ARCHITECTURE.md ("HTTP
and dashboard").
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

RFQ_RESULT = ModelEnvelope.model_validate({"result": VALID_RFQ}).result


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


def test_empty_state(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "No RFQs ingested yet" in response.text


def test_ingested_rfq_appears_after_refresh_with_all_fields_and_warnings(client):
    client.app.state.extractor = lambda bundle: RFQ_RESULT

    ingest_response = client.post(
        "/ingest", content=_raw_email(), headers={"content-type": "message/rfc822"}
    )
    assert ingest_response.status_code == 200

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
    non_rfq = ModelEnvelope.model_validate(
        {"result": {"isRfq": False, "confidence": 0.9, "reason": "Newsletter."}}
    ).result
    client.app.state.extractor = lambda bundle: non_rfq
    client.post("/ingest", content=_raw_email(), headers={"content-type": "message/rfc822"})

    page = client.get("/").text
    assert "No RFQs ingested yet" in page


def test_newest_rfq_appears_first(client):
    client.app.state.extractor = lambda bundle: RFQ_RESULT
    client.post(
        "/ingest",
        content=_raw_email(subject="First RFQ"),
        headers={"content-type": "message/rfc822"},
    )
    client.post(
        "/ingest",
        content=_raw_email(subject="Second RFQ"),
        headers={"content-type": "message/rfc822"},
    )

    page = client.get("/").text
    assert page.index("Second RFQ") < page.index("First RFQ")


def test_untrusted_text_is_escaped_not_executed(client):
    malicious = ModelEnvelope.model_validate(
        {
            "result": {
                **VALID_RFQ,
                "warnings": ['<script>document.title="pwned"</script>'],
                "customer": {**VALID_RFQ["customer"], "name": "<img src=x onerror=alert(1)>"},
            }
        }
    ).result
    client.app.state.extractor = lambda bundle: malicious
    client.post("/ingest", content=_raw_email(), headers={"content-type": "message/rfc822"})

    page = client.get("/").text
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert "<img src=x" not in page
    assert "&lt;img" in page
