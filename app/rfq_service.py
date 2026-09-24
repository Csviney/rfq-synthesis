"""Orchestration: parse -> extract -> finalize. No storage here — that's
POST /ingest's job.
"""

from typing import Callable

from app.attachment_parser import build_source_bundle
from app.email_parser import parse_email
from app.exceptions import NoUsableSourceTextError
from app.models import ModelEnvelope, ParsedEmail, RfqResult, SourceBundle, TriageRecommendation

Extractor = Callable[[SourceBundle], ModelEnvelope]


def process_email(raw: bytes, extract: Extractor) -> ModelEnvelope:
    """`extract` is injected so tests can use a fake extractor instead of
    the real model; app wiring passes
    `functools.partial(llm_client.extract, client, model)`."""
    parsed = parse_email(raw)
    bundle = build_source_bundle(parsed)

    if not _has_usable_source_text(bundle):
        raise NoUsableSourceTextError("No usable source text found in the email or attachments")

    envelope = extract(bundle)

    if isinstance(envelope.result, RfqResult):
        result = _finalize_rfq(envelope.result, parsed)
        triage = _reconcile_triage(envelope.triage, result, parsed)
        envelope = ModelEnvelope(result=result, triage=triage)

    return envelope


def _has_usable_source_text(bundle: SourceBundle) -> bool:
    return any(chunk.text.strip() for chunk in bundle.chunks if chunk.name != "email headers")


def _finalize_rfq(result: RfqResult, parsed: ParsedEmail) -> RfqResult:
    """Merge parser warnings, flag (without changing) a due date before the
    email date, and flag an RFQ with no identifiable items. Never corrects
    a model-stated fact — only adds warnings."""
    warnings = list(dict.fromkeys(result.warnings + parsed.warnings))  # dedupe, keep order

    if result.request.dueDate is not None and parsed.date is not None:
        email_date = parsed.date.date()
        if result.request.dueDate < email_date:
            warnings.append(
                f"Requested due date {result.request.dueDate} is before the email date {email_date}"
            )

    if not result.lineItems:
        warnings.append("RFQ has no identifiable line items")

    return result.model_copy(update={"warnings": warnings})


def _reconcile_triage(
    triage: TriageRecommendation, result: RfqResult, parsed: ParsedEmail
) -> TriageRecommendation:
    """Guarantee clarify_with_customer when a structured fact requires it,
    regardless of what the model recommended — checked in priority order,
    first match wins. Deliberately checks structured fields (dueDate,
    lineItems, quantity), not warning text, so it isn't fooled by how a
    warning happens to be worded.

    If the model already reached clarify_with_customer on its own, its
    reason is likely more specific than our generic fallback (e.g. it may
    have caught the actual conflict behind a quantity that only reads as
    "0" once extracted, such as "120 or 240, not sure which"), so that's
    kept rather than replaced. The generic fallback only fires when the
    model's own category was wrong, to still guarantee the category is
    correct even then."""
    if result.request.dueDate is not None and parsed.date is not None:
        email_date = parsed.date.date()
        if result.request.dueDate < email_date:
            if triage.category == "clarify_with_customer":
                return triage
            return TriageRecommendation(
                category="clarify_with_customer",
                reason=f"Requested due date {result.request.dueDate} is before the email date {email_date}.",
                nextStep="Confirm the intended due date with the customer.",
            )

    if not result.lineItems:
        if triage.category == "clarify_with_customer":
            return triage
        return TriageRecommendation(
            category="clarify_with_customer",
            reason="No identifiable line items were extracted from this RFQ.",
            nextStep="Request a parts list from the customer.",
        )

    # quantity 0 means "not stated" per the extraction contract, but the
    # schema can't distinguish that from a genuinely-stated zero, so this
    # can't claim the quantity was never stated — only that it's zero and
    # worth confirming before pricing.
    zero_quantity_parts = [item.partNumber for item in result.lineItems if item.quantity == 0]
    if zero_quantity_parts:
        if triage.category == "clarify_with_customer":
            return triage
        parts = ", ".join(zero_quantity_parts)
        return TriageRecommendation(
            category="clarify_with_customer",
            reason=f"These items have zero quantities; confirm the intended quantities before pricing: {parts}.",
            nextStep=f"Ask the customer to confirm the quantity for: {parts}.",
        )

    return triage
