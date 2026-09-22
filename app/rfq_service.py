"""Orchestration: parse -> extract -> finalize. No storage here — that's
POST /ingest's job in a later step. See architecture/DATA_FLOW.md ("Request
lifecycle" and "After extraction...").
"""

from typing import Callable

from app.attachment_parser import build_source_bundle
from app.email_parser import parse_email
from app.models import NonRfqResult, NoUsableSourceTextError, ParsedEmail, RfqResult, SourceBundle

Extractor = Callable[[SourceBundle], RfqResult | NonRfqResult]


def process_email(raw: bytes, extract: Extractor) -> RfqResult | NonRfqResult:
    """`extract` is injected so tests can use a fake extractor instead of
    the real model; app wiring passes
    `functools.partial(llm_client.extract, client, model)`."""
    parsed = parse_email(raw)
    bundle = build_source_bundle(parsed)

    if not _has_usable_source_text(bundle):
        raise NoUsableSourceTextError("No usable source text found in the email or attachments")

    result = extract(bundle)

    if isinstance(result, RfqResult):
        result = _finalize_rfq(result, parsed)

    return result


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
