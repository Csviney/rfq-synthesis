"""Local validation of the public contract and provider envelope from
architecture/LLM_DESIGN.md. No network access; this is the "both response
shapes validate locally" half of Implementation Plan step 1's completion
check.
"""

import pytest
from pydantic import ValidationError

from app.models import LineItem, ModelEnvelope, NonRfqResult, RfqResult

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


def test_valid_rfq_validates():
    result = RfqResult.model_validate(VALID_RFQ)
    assert result.isRfq is True
    assert result.lineItems[0].partNumber == "LM358N"


def test_valid_non_rfq_validates():
    result = NonRfqResult.model_validate(VALID_NON_RFQ)
    assert result.isRfq is False
    assert result.reason


def test_envelope_discriminates_rfq_and_non_rfq():
    rfq_envelope = ModelEnvelope.model_validate({"result": VALID_RFQ})
    assert isinstance(rfq_envelope.result, RfqResult)

    non_rfq_envelope = ModelEnvelope.model_validate({"result": VALID_NON_RFQ})
    assert isinstance(non_rfq_envelope.result, NonRfqResult)


def test_ingest_response_excludes_provider_wrapper():
    envelope = ModelEnvelope.model_validate({"result": VALID_RFQ})
    dumped = envelope.result.model_dump(mode="json")
    assert "result" not in dumped
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
        ModelEnvelope.model_validate({"result": {"isRfq": True}})
