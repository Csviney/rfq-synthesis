"""Opt-in checks that hit the real, paid provider. Per
architecture/IMPLEMENTATION_PLAN.md's "Live" testing policy: a mock
returning expected data does not prove model behavior, so these are never
faked. Skipped unless explicitly enabled with RUN_LIVE_TESTS=1 *and*
OPENAI_API_KEY/OPENAI_MODEL are set — credentials alone are not consent to
spend money on every `pytest tests/` run, e.g. in a dev environment where a
.env with real keys is already loaded.

Step 1 added the schema smoke test (proves ModelEnvelope/RfqResult/
NonRfqResult are accepted by the provider's structured-output support).
Step 3 adds the two checks its own completion check calls for: a body RFQ
and a non-RFQ working end to end through the real model. The full sample
matrix (ambiguity, both negatives, the restock injection, body/PDF
completeness) is step 6's acceptance pass, not this one's.
"""

import functools
import os
from pathlib import Path

import pytest

from app.llm_client import LlmConfig, LlmConfigError, build_client, extract
from app.models import ModelEnvelope, NonRfqResult, RfqResult
from app.rfq_service import process_email

SAMPLES_DIR = Path(__file__).parent.parent / "samples"

pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("RUN_LIVE_TESTS") == "1"
        and os.environ.get("OPENAI_API_KEY")
        and os.environ.get("OPENAI_MODEL")
    ),
    reason="live provider check skipped; set RUN_LIVE_TESTS=1 with OPENAI_API_KEY/OPENAI_MODEL to opt in",
)


def test_provider_accepts_model_envelope_schema():
    try:
        config = LlmConfig.from_env()
    except LlmConfigError as exc:
        pytest.skip(str(exc))

    client = build_client(config)
    try:
        completion = client.beta.chat.completions.parse(
            model=config.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Reply with a NonRfqResult: isRfq=false, confidence=1, "
                        "reason='schema smoke test'."
                    ),
                },
                {"role": "user", "content": "schema smoke test"},
            ],
            response_format=ModelEnvelope,
        )
    finally:
        client.close()

    parsed = completion.choices[0].message.parsed
    assert isinstance(parsed, ModelEnvelope)
    assert isinstance(parsed.result, (RfqResult, NonRfqResult))


def _live_extractor():
    try:
        config = LlmConfig.from_env()
    except LlmConfigError as exc:
        pytest.skip(str(exc))
    client = build_client(config)
    return client, functools.partial(extract, client, config.model)


def test_body_rfq_sample_works_with_the_real_model():
    """architecture/DATA_FLOW.md's sample table: rfq-01-bullet.eml is an
    RFQ with five items: TI LM358N x500, ATMEGA328P-PU x250, BC547 x1000,
    a 10K resistor x5000, a 100nF capacitor x3000. Checking identifier ->
    quantity pairs (not just the two sets independently) catches a model
    that mismatches which part gets which quantity; substring matching
    (not exact partNumber equality) tolerates real-model wording
    differences on the two generic, MPN-less passive parts."""
    client, extractor = _live_extractor()
    try:
        raw = (SAMPLES_DIR / "rfq-01-bullet.eml").read_bytes()
        result = process_email(raw, extractor)
    finally:
        client.close()

    assert isinstance(result, RfqResult)
    assert len(result.lineItems) == 5

    expected = {"LM358N": 500, "ATMEGA328P-PU": 250, "BC547": 1000, "10K": 5000, "100NF": 3000}
    found = {}
    for item in result.lineItems:
        haystack = f"{item.partNumber} {item.description or ''}".upper()
        for key in expected:
            if key in haystack:
                found[key] = item.quantity
    assert found == expected


def test_non_rfq_sample_works_with_the_real_model():
    """architecture/DATA_FLOW.md: the order confirmation is a non-RFQ
    despite containing parts, quantities, and prices."""
    client, extractor = _live_extractor()
    try:
        raw = (SAMPLES_DIR / "not-an-rfq-01-order-confirmation.eml").read_bytes()
        result = process_email(raw, extractor)
    finally:
        client.close()

    assert isinstance(result, NonRfqResult)
    assert result.reason.strip()
