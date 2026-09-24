"""Local validation of the public contract and provider envelope."""

import pytest
from pydantic import ValidationError

from app.models import LineItem, ModelEnvelope, NonRfqResult, RfqResult, TriageRecommendation

VALID_RFQ = {
    "isRfq": True,
    "confidence": 0.9,
    "customer": {
        "name": None,
        "email": None,
        "phone": None,
        "company": None,
    },
    "request": {
        "dueDate": None,
        "priority": "medium",
        "specialInstructions": None,
    },
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

VALID_NON_RFQ = {
    "isRfq": False,
    "confidence": 0.95,
    "reason": "Order confirmation, not a request for quotation.",
}

VALID_TRIAGE = {
    "category": "begin_pricing",
    "reason": "All parts are identified with an explicit quantity.",
    "nextStep": "Check unit pricing and lead times.",
}


def test_valid_rfq_validates():
    result = RfqResult.model_validate(VALID_RFQ)
    assert result.isRfq is True
    assert result.lineItems[0].partNumber == "LM358N"


def test_valid_non_rfq_validates():
    result = NonRfqResult.model_validate(VALID_NON_RFQ)
    assert result.isRfq is False
    assert result.reason


def test_envelope_discriminates_rfq_and_non_rfq():
    rfq_envelope = ModelEnvelope.model_validate({"result": VALID_RFQ, "triage": VALID_TRIAGE})
    assert isinstance(rfq_envelope.result, RfqResult)

    non_rfq_envelope = ModelEnvelope.model_validate({"result": VALID_NON_RFQ, "triage": None})
    assert isinstance(non_rfq_envelope.result, NonRfqResult)


def test_ingest_response_excludes_provider_wrapper():
    envelope = ModelEnvelope.model_validate({"result": VALID_RFQ, "triage": VALID_TRIAGE})
    dumped = envelope.result.model_dump(mode="json")
    assert "result" not in dumped
    assert "triage" not in dumped
    assert dumped["isRfq"] is True


def test_dates_serialize_as_yyyy_mm_dd():
    rfq = dict(VALID_RFQ)
    rfq["request"] = {**VALID_RFQ["request"], "dueDate": "2026-03-15"}
    result = RfqResult.model_validate(rfq)
    assert result.model_dump(mode="json")["request"]["dueDate"] == "2026-03-15"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(extraField="not allowed"),
        lambda d: d["customer"].__setitem__("extra", "nope"),
        lambda d: d["lineItems"][0].__setitem__("extra", "nope"),
    ],
)
def test_extra_fields_are_rejected(mutation):
    payload = {
        "isRfq": True,
        "confidence": 0.9,
        "customer": dict(VALID_RFQ["customer"]),
        "request": dict(VALID_RFQ["request"]),
        "lineItems": [dict(VALID_RFQ["lineItems"][0])],
        "warnings": [],
    }
    mutation(payload)
    with pytest.raises(ValidationError):
        RfqResult.model_validate(payload)


@pytest.mark.parametrize(
    "key",
    ["isRfq", "confidence", "customer", "request", "lineItems", "warnings"],
)
def test_missing_required_key_is_rejected(key):
    payload = dict(VALID_RFQ)
    payload["customer"] = dict(VALID_RFQ["customer"])
    payload["request"] = dict(VALID_RFQ["request"])
    payload["lineItems"] = [dict(VALID_RFQ["lineItems"][0])]
    del payload[key]
    with pytest.raises(ValidationError):
        RfqResult.model_validate(payload)


def test_null_value_for_required_key_is_accepted():
    payload = dict(VALID_RFQ)
    payload["customer"] = {**VALID_RFQ["customer"], "name": None}
    RfqResult.model_validate(payload)


@pytest.mark.parametrize(
    "confidence",
    [-0.01, 1.01, float("nan"), float("inf"), float("-inf")],
)
def test_confidence_out_of_range_or_non_finite_is_rejected(confidence):
    payload = dict(VALID_RFQ)
    payload["confidence"] = confidence
    with pytest.raises(ValidationError):
        RfqResult.model_validate(payload)


def test_confidence_accepts_bare_integer():
    payload = dict(VALID_RFQ)
    payload["confidence"] = 1
    result = RfqResult.model_validate(payload)
    assert result.confidence == 1.0


@pytest.mark.parametrize("confidence", ["0.9", True, False])
def test_confidence_does_not_coerce_from_string_or_bool(confidence):
    payload = dict(VALID_RFQ)
    payload["confidence"] = confidence
    with pytest.raises(ValidationError):
        RfqResult.model_validate(payload)


def test_negative_quantity_is_rejected():
    with pytest.raises(ValidationError):
        LineItem.model_validate(
            {
                "partNumber": "LM358N",
                "manufacturer": None,
                "description": None,
                "quantity": -1,
                "targetPrice": None,
                "notes": None,
            }
        )


def test_quantity_does_not_coerce_from_string():
    with pytest.raises(ValidationError):
        LineItem.model_validate(
            {
                "partNumber": "LM358N",
                "manufacturer": None,
                "description": None,
                "quantity": "500",
                "targetPrice": None,
                "notes": None,
            }
        )


def test_blank_part_number_is_rejected():
    with pytest.raises(ValidationError):
        LineItem.model_validate(
            {
                "partNumber": "   ",
                "manufacturer": None,
                "description": None,
                "quantity": 1,
                "targetPrice": None,
                "notes": None,
            }
        )


def test_negative_target_price_is_rejected():
    with pytest.raises(ValidationError):
        LineItem.model_validate(
            {
                "partNumber": "LM358N",
                "manufacturer": None,
                "description": None,
                "quantity": 1,
                "targetPrice": -5.0,
                "notes": None,
            }
        )


def test_target_price_accepts_bare_integer():
    item = LineItem.model_validate(
        {
            "partNumber": "LM358N",
            "manufacturer": None,
            "description": None,
            "quantity": 1,
            "targetPrice": 5,
            "notes": None,
        }
    )
    assert item.targetPrice == 5.0


@pytest.mark.parametrize("target_price", ["1.1", True, False])
def test_target_price_does_not_coerce_from_string_or_bool(target_price):
    with pytest.raises(ValidationError):
        LineItem.model_validate(
            {
                "partNumber": "LM358N",
                "manufacturer": None,
                "description": None,
                "quantity": 1,
                "targetPrice": target_price,
                "notes": None,
            }
        )


def test_invalid_priority_is_rejected():
    payload = dict(VALID_RFQ)
    payload["request"] = {**VALID_RFQ["request"], "priority": "asap"}
    with pytest.raises(ValidationError):
        RfqResult.model_validate(payload)


@pytest.mark.parametrize(
    "due_date",
    [
        "March 15, 2026",
        "2026-02-30",  # not a real calendar date
        "2026-03-15T00:00:00",  # datetime string, not date-only
        "2026-03-15T12:30:00Z",
        0,  # would be read as a Unix timestamp by lax date parsing
        "0",
        1700000000,
    ],
)
def test_malformed_due_date_is_rejected(due_date):
    payload = dict(VALID_RFQ)
    payload["request"] = {**VALID_RFQ["request"], "dueDate": due_date}
    with pytest.raises(ValidationError):
        RfqResult.model_validate(payload)


def test_is_rfq_does_not_coerce_from_int():
    payload = dict(VALID_RFQ)
    payload["isRfq"] = 1
    with pytest.raises(ValidationError):
        RfqResult.model_validate(payload)


def test_envelope_rejects_result_missing_discriminator_match():
    with pytest.raises(ValidationError):
        ModelEnvelope.model_validate({"result": {"isRfq": True}, "triage": None})


def test_valid_triage_validates():
    triage = TriageRecommendation.model_validate(VALID_TRIAGE)
    assert triage.category == "begin_pricing"


@pytest.mark.parametrize("category", ["begin_pricing", "review_sourcing", "clarify_with_customer"])
def test_all_three_triage_categories_validate(category):
    TriageRecommendation.model_validate({**VALID_TRIAGE, "category": category})


def test_invalid_triage_category_is_rejected():
    with pytest.raises(ValidationError):
        TriageRecommendation.model_validate({**VALID_TRIAGE, "category": "expedite"})


@pytest.mark.parametrize("field", ["reason", "nextStep"])
@pytest.mark.parametrize("blank_value", ["", "   "])
def test_blank_triage_text_is_rejected(field, blank_value):
    with pytest.raises(ValidationError):
        TriageRecommendation.model_validate({**VALID_TRIAGE, field: blank_value})


def test_envelope_requires_triage_when_result_is_an_rfq():
    with pytest.raises(ValidationError):
        ModelEnvelope.model_validate({"result": VALID_RFQ, "triage": None})


def test_envelope_requires_missing_triage_key_is_rejected_for_rfq():
    with pytest.raises(ValidationError):
        ModelEnvelope.model_validate({"result": VALID_RFQ})


def test_envelope_requires_triage_to_be_null_when_result_is_not_an_rfq():
    with pytest.raises(ValidationError):
        ModelEnvelope.model_validate({"result": VALID_NON_RFQ, "triage": VALID_TRIAGE})


def test_envelope_accepts_null_triage_for_non_rfq():
    envelope = ModelEnvelope.model_validate({"result": VALID_NON_RFQ, "triage": None})
    assert envelope.triage is None


def test_triage_rejects_extra_fields():
    with pytest.raises(ValidationError):
        TriageRecommendation.model_validate({**VALID_TRIAGE, "confidence": 0.9})
