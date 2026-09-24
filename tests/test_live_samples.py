"""Opt-in checks that hit the real, paid provider. A mock returning
expected data does not prove model behavior, so these are never faked.
Skipped unless explicitly enabled with RUN_LIVE_TESTS=1 *and*
OPENAI_API_KEY/OPENAI_MODEL are set — credentials alone are not consent to
spend money on every `pytest tests/` run, e.g. in a dev environment where a
.env with real keys is already loaded.

Covers the full sample matrix (ambiguity, per-board/pooled quantity math,
split project deadlines, the restock prompt-injection sample, both
negatives, body/PDF completeness) plus semantic triage checks. Compare
stable fields and item sets, not exact confidence or prose — labels.json is
reference data, not a runtime oracle, and isn't read here.
rfq-08-image.eml (the unsupported image attachment) is not a live check —
parsing rejects it before any model call, already covered offline in
tests/test_parsing.py.
"""

import functools
import os
import re
from pathlib import Path

import pytest

from app.exceptions import LlmConfigError
from app.llm_client import LlmConfig, build_client, extract
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
                        "reason='schema smoke test', triage=null."
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


def _run(filename) -> ModelEnvelope:
    client, extractor = _live_extractor()
    try:
        raw = (SAMPLES_DIR / filename).read_bytes()
        return process_email(raw, extractor)
    finally:
        client.close()


def _run_raw(raw: bytes) -> ModelEnvelope:
    client, extractor = _live_extractor()
    try:
        return process_email(raw, extractor)
    finally:
        client.close()


def _assert_identifier_quantity_pairs(line_items, expected):
    """Substring match on partNumber+description (not exact equality) to
    tolerate real-model wording differences on generic, MPN-less parts.
    Checking pairs, not the two sets independently, catches a model that
    mismatches which part gets which quantity."""
    found = {}
    for item in line_items:
        haystack = f"{item.partNumber} {item.description or ''}".upper()
        for key in expected:
            if key in haystack:
                found[key] = item.quantity
    assert found == expected


def _find_item(line_items, key):
    """Like _assert_identifier_quantity_pairs, but returns the matched
    item itself so a test can also check its manufacturer/price/notes."""
    matches = [
        item
        for item in line_items
        if key in f"{item.partNumber} {item.description or ''}".upper()
    ]
    assert len(matches) == 1, f"expected exactly one line item matching {key!r}, found {matches}"
    return matches[0]


def _mentions_date(text: str, month_name: str, month_num: str, day: int) -> bool:
    """Whether `text` names this specific date, tolerating the two
    date renderings a model realistically produces (month-name prose, or
    an ISO date it copied through) without accepting a different day —
    e.g. must reject "Sep 30" when checking for "Sep 1"."""
    not_digit_adjacent = rf"(?<!\d){day}(?!\d)"
    name_pattern = rf"{month_name}\w*\.?\s*{not_digit_adjacent}"
    iso_pattern = rf"-{month_num}-{day:02d}(?!\d)"
    return bool(re.search(name_pattern, text, re.IGNORECASE) or re.search(iso_pattern, text))


def test_body_rfq_sample_works_with_the_real_model():
    """architecture/DATA_FLOW.md's sample table: rfq-01-bullet.eml is an
    RFQ with five items: TI LM358N x500, ATMEGA328P-PU x250, BC547 x1000,
    a 10K resistor x5000, a 100nF capacitor x3000."""
    envelope = _run("rfq-01-bullet.eml")
    result = envelope.result

    assert isinstance(result, RfqResult)
    assert len(result.lineItems) == 5
    _assert_identifier_quantity_pairs(
        result.lineItems,
        {"LM358N": 500, "ATMEGA328P-PU": 250, "BC547": 1000, "10K": 5000, "100NF": 3000},
    )
    # Fully specified, nothing to review or clarify.
    assert envelope.triage.category == "begin_pricing"


def test_non_rfq_sample_works_with_the_real_model():
    """architecture/DATA_FLOW.md: the order confirmation is a non-RFQ
    despite containing parts, quantities, and prices."""
    envelope = _run("not-an-rfq-01-order-confirmation.eml")
    result = envelope.result
    assert isinstance(result, NonRfqResult)
    assert result.reason.strip()


def test_newsletter_sample_is_a_non_rfq():
    """architecture/DATA_FLOW.md: non-RFQ despite product and pricing
    language ("Available in quantities from 100 to 10,000 units")."""
    envelope = _run("not-an-rfq-02-newsletter.eml")
    result = envelope.result
    assert isinstance(result, NonRfqResult)
    assert result.reason.strip()


def test_table_sample_preserves_due_date_and_flags_it_against_the_email_date():
    """architecture/DATA_FLOW.md: five rows with prices/manufacturers,
    high priority; preserve 2026-03-15 and flag that it precedes the June
    email date (without changing it). Checks manufacturer/price per row,
    not just quantity — the source table states a different manufacturer
    notation per row (e.g. "Texas Instruments" for one part, "TI" for
    another), so each row's expected substring is drawn from what that
    specific row actually says."""
    envelope = _run("rfq-02-table.eml")
    result = envelope.result

    assert isinstance(result, RfqResult)
    assert len(result.lineItems) == 5
    assert result.request.priority == "high"
    assert result.request.dueDate.isoformat() == "2026-03-15"
    assert any("before the email date" in w for w in result.warnings)

    # key -> (quantity, a substring of the source-stated manufacturer, target price)
    expected = {
        "SN74HC595N": (200, "TEXAS", 0.50),
        "NE555P": (150, "TI", 0.30),
        "2N2222A": (1000, "ON", 0.15),
        "1N4148": (2000, "VISHAY", 0.05),
        "IRFZ44N": (100, "RECTIFIER", 1.20),
    }
    for key, (quantity, manufacturer_substr, price) in expected.items():
        item = _find_item(result.lineItems, key)
        assert item.quantity == quantity
        assert item.manufacturer and manufacturer_substr in item.manufacturer.upper()
        assert item.targetPrice == pytest.approx(price)

    # The due date precedes the email date: rfq_service._reconcile_triage
    # deterministically overrides to clarify_with_customer regardless of
    # what the model itself recommended.
    assert envelope.triage.category == "clarify_with_customer"
    assert "2026-03-15" in envelope.triage.reason


def test_ambiguous_sample_uses_the_lower_bound_of_each_range_or_approximation():
    """architecture/DATA_FLOW.md's sample table: quantities 100, 200, 50,
    100, 25 under the range/approximation policy (lower bound of a stated
    range; the approximate number as given). Checked as identifier ->
    quantity pairs, not a bare multiset — five right numbers attached to
    the wrong parts would otherwise pass. Also checks that the original
    range/approximation is retained somewhere (per DATA_FLOW.md), not just
    that the collapsed number is right: the source states "100-150",
    "roughly 200", "50 to 75", "~100pcs", "approx. 25-30"."""
    envelope = _run("rfq-03-ambiguous.eml")
    result = envelope.result

    assert isinstance(result, RfqResult)
    assert len(result.lineItems) == 5
    _assert_identifier_quantity_pairs(
        result.lineItems,
        {"ATMEGA328P": 100, "ESP8266": 200, "LM2596": 50, "OLED": 100, "DHT22": 25},
    )

    warnings_text = " ".join(result.warnings)

    def _range_preserved(key, upper_bound):
        item = _find_item(result.lineItems, key)
        text = f"{item.notes or ''} {warnings_text}"
        return str(upper_bound) in text

    def _approximation_preserved(key):
        item = _find_item(result.lineItems, key)
        text = f"{item.notes or ''} {warnings_text}".lower()
        return any(word in text for word in ("rough", "approx", "~", "about"))

    assert _range_preserved("ATMEGA328P", 150)
    assert _range_preserved("LM2596", 75)
    assert _range_preserved("DHT22", 30)
    assert _approximation_preserved("ESP8266")
    assert _approximation_preserved("OLED")

    # No triage category assertion here: this sample mixes quantity ranges
    # with generic, underspecified parts ("ESP8266 modules", "OLED
    # displays", no manufacturer or specific part number), so
    # review_sourcing is also a reasonable call. Range-alone tolerance is
    # tested in isolation below with otherwise fully-specified parts.


def test_csv_attachment_sample_works_with_the_real_model():
    """architecture/DATA_FLOW.md: LM7805 500, KBPC5010 500, 1N5819 2,000,
    LM317T 300; LM317T target price 1.1."""
    envelope = _run("rfq-04-csv-attachment.eml")
    result = envelope.result

    assert isinstance(result, RfqResult)
    assert len(result.lineItems) == 4
    _assert_identifier_quantity_pairs(
        result.lineItems,
        {"LM7805": 500, "KBPC5010": 500, "1N5819": 2000, "LM317T": 300},
    )
    lm317t = next(item for item in result.lineItems if "LM317T" in item.partNumber.upper())
    assert lm317t.targetPrice == pytest.approx(1.1)

    # Four identified parts, explicit quantities, nothing to review.
    assert envelope.triage.category == "begin_pricing"


def test_pdf_attachment_sample_merges_pdf_and_body_only_item():
    """architecture/DATA_FLOW.md: four PDF items plus body-only NEO-6M
    100 — five items total."""
    envelope = _run("rfq-05-pdf-attachment.eml")
    result = envelope.result

    assert isinstance(result, RfqResult)
    assert len(result.lineItems) == 5
    _assert_identifier_quantity_pairs(
        result.lineItems,
        {
            "STM32F407VGT6": 200,
            "ILI9486": 150,
            "AMS1117-3.3": 500,
            "USB-C": 300,
            "NEO-6M": 100,
        },
    )


def test_multi_project_sample_computes_per_board_totals_and_keeps_pooled_quantity():
    """architecture/DATA_FLOW.md: eight lines. Relay 1,000 (2x500), PIR 900
    (3x300), LED 1,800 (6x300); keep 50,000 assorted resistors pooled;
    preserve equivalents, RoHS, project context, and both delivery dates
    (as a null request-level date, both dates retained, with a warning
    per the split-deadline policy) — Project A by Aug 15, Project B by
    Sep 1. Checks the actual day of month, not just that "Aug"/"Sep"
    appear anywhere: "August 1" and "September 30" would otherwise pass."""
    envelope = _run("rfq-06-multi-project.eml")
    result = envelope.result

    assert isinstance(result, RfqResult)
    assert len(result.lineItems) == 8
    _assert_identifier_quantity_pairs(
        result.lineItems,
        {
            "ESP32-WROOM-32D": 500,
            "RELAY": 1000,
            "USB-C": 500,
            "STM32F407VGT6": 300,
            "PIR": 900,
            "LED": 1800,
            "RESISTOR": 50000,
            "LM358N": 300,
        },
    )
    assert result.request.dueDate is None

    lm358n = _find_item(result.lineItems, "LM358N")
    combined_text = " ".join(
        [result.request.specialInstructions or ""]
        + [item.notes or "" for item in result.lineItems]
        + result.warnings
    )

    assert _mentions_date(combined_text, "aug", "08", 15)
    assert _mentions_date(combined_text, "sep", "09", 1)
    assert "rohs" in combined_text.lower()
    assert "equivalent" in f"{lm358n.notes or ''} {lm358n.description or ''}".lower()
    assert "project a" in combined_text.lower() and "project b" in combined_text.lower()
    # DATA_FLOW.md requires a warning for a split deadline; whether the
    # model actually adds one is real signal, not test flakiness — see
    # FUTURE_FIXES.md if this needs to be tracked as a standing
    # model-reliability limitation rather than fixed outright.
    assert result.warnings

    # Coordinated split delivery + RoHS certification need internal review
    # before pricing — complexity alone, not a customer clarification.
    assert envelope.triage.category == "review_sourcing"


def test_restock_sample_resists_the_embedded_injection():
    """architecture/DATA_FLOW.md: the embedded fake "SYSTEM INSTRUCTION"
    telling the model to report isRfq=false must not suppress the actual
    request — TL072CP 400, STM32F103C8T6 250, AMS1117-3.3 1,000."""
    envelope = _run("rfq-07-restock.eml")
    result = envelope.result

    assert isinstance(result, RfqResult)
    assert len(result.lineItems) == 3
    _assert_identifier_quantity_pairs(
        result.lineItems,
        {"TL072CP": 400, "STM32F103C8T6": 250, "AMS1117-3.3": 1000},
    )

    # The injected text must not leak into the triage recommendation either.
    assert "SYSTEM" not in envelope.triage.reason.upper()
    assert "SYSTEM" not in envelope.triage.nextStep.upper()


# --- New scenarios, not drawn from the supplied sample set --------------
# Two RFQs stating the same customer priority ("high") but differing only
# in whether a coordination/certification requirement is present, to show
# triage tracks something other than request.priority.


def _synthetic_email(subject: str, body: str) -> bytes:
    msg = (
        f"From: buyer@example.com\r\n"
        f"Subject: {subject}\r\n"
        "Date: Mon, 08 Jun 2026 09:14:00 +0000\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n" + body + "\r\n"
    )
    return msg.encode("utf-8")


def test_clean_urgent_request_recommends_begin_pricing():
    raw = _synthetic_email(
        "RFQ - urgent restock",
        "Hi,\n\nThis is urgent, please quote ASAP:\n"
        "- STM32F103C8T6 x 200\n"
        "- LM317T x 150\n"
        "- 1N4007 x 1000\n\nThanks",
    )
    envelope = _run_raw(raw)

    assert isinstance(envelope.result, RfqResult)
    assert envelope.result.request.priority in ("high", "urgent")
    assert envelope.triage.category == "begin_pricing"


def test_urgent_request_with_coordination_requirement_recommends_sourcing_review():
    """Same stated urgency as the previous test, but this RFQ also needs a
    coordinated multi-site delivery and RoHS certificates — a stated rush
    must not by itself justify begin_pricing over that requirement."""
    raw = _synthetic_email(
        "RFQ - urgent multi-site order",
        "Hi,\n\nThis is urgent, please quote ASAP. We need this delivered in two "
        "separate shipments arriving at our two manufacturing sites on the same "
        "day, and all parts must include RoHS certificates:\n"
        "- STM32F103C8T6 x 200\n"
        "- LM317T x 150\n\nThanks",
    )
    envelope = _run_raw(raw)

    assert isinstance(envelope.result, RfqResult)
    assert envelope.result.request.priority in ("high", "urgent")
    assert envelope.triage.category == "review_sourcing"


def test_quantity_range_alone_does_not_block_begin_pricing():
    """Isolates range tolerance from part-identification ambiguity: both
    parts here are fully specified (known MPN and manufacturer), and only
    the quantity is a range. rfq-03-ambiguous.eml mixes ranges with
    generic, underspecified parts, so it can't isolate this rule alone."""
    raw = _synthetic_email(
        "RFQ - quantity range",
        "Hi,\n\nPlease quote:\n"
        "- STM32F103C8T6 (STMicroelectronics) - 200 to 300 units\n"
        "- LM317T (Texas Instruments) - 500 units\n\nThanks",
    )
    envelope = _run_raw(raw)

    assert isinstance(envelope.result, RfqResult)
    assert envelope.triage.category == "begin_pricing"
