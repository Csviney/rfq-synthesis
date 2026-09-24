"""Email/CSV/PDF parsing and source-bundle completeness. No AI: this only
exercises app/email_parser.py and app/attachment_parser.py against the
supplied samples and a few synthetic edge cases. See
architecture/DATA_FLOW.md ("Sample acceptance checks") for what each
sample must retain.
"""

from email import policy
from email.message import EmailMessage
from pathlib import Path

import pytest

from app.attachment_parser import build_source_bundle
from app.exceptions import UnsupportedAttachmentError
from app.email_parser import EmailParseError, parse_email
from app.models import EmailAttachment, ParsedEmail

SAMPLES_DIR = Path(__file__).parent.parent / "samples"


def _load(name: str) -> bytes:
    return (SAMPLES_DIR / name).read_bytes()


def _bundle_text(name: str) -> str:
    parsed = parse_email(_load(name))
    bundle = build_source_bundle(parsed)
    return "\n".join(chunk.text for chunk in bundle.chunks)


@pytest.mark.parametrize(
    "name",
    [
        "rfq-01-bullet.eml",
        "rfq-02-table.eml",
        "rfq-03-ambiguous.eml",
        "rfq-04-csv-attachment.eml",
        "rfq-05-pdf-attachment.eml",
        "rfq-06-multi-project.eml",
        "rfq-07-restock.eml",
        "not-an-rfq-01-order-confirmation.eml",
        "not-an-rfq-02-newsletter.eml",
    ],
)
def test_supported_samples_parse_without_ai(name):
    parsed = parse_email(_load(name))
    bundle = build_source_bundle(parsed)

    # Every chunk must actually carry content, not just the always-nonempty
    # headers chunk — a body or attachment that silently disappeared would
    # otherwise still pass an "any chunk is nonempty" check.
    assert bundle.chunks
    body_chunk = next(c for c in bundle.chunks if c.name == "email body")
    assert body_chunk.text.strip()
    assert all(chunk.text.strip() for chunk in bundle.chunks)


def test_image_sample_is_an_explicit_unsupported_content_error():
    parsed = parse_email(_load("rfq-08-image.eml"))
    with pytest.raises(UnsupportedAttachmentError):
        build_source_bundle(parsed)


def test_bullet_sample_retains_stated_quantities():
    text = _bundle_text("rfq-01-bullet.eml")
    for quantity in ("500", "250", "1000", "5000", "3000"):
        assert quantity in text


def test_table_sample_retains_due_date():
    # Parsing preserves source text verbatim; resolving "March 15, 2026" to
    # an ISO date is an extraction-step (step 3) decision, not this one's.
    text = _bundle_text("rfq-02-table.eml")
    assert "March 15, 2026" in text


def test_csv_attachment_chunk_preserves_rows_and_values():
    parsed = parse_email(_load("rfq-04-csv-attachment.eml"))
    bundle = build_source_bundle(parsed)
    csv_chunks = [c for c in bundle.chunks if "CSV" in c.name]
    assert len(csv_chunks) == 1

    csv_text = csv_chunks[0].text
    for value in ("LM7805", "KBPC5010", "1N5819", "LM317T", "500", "2000", "300", "1.1"):
        assert value in csv_text


def test_pdf_attachment_yields_one_chunk_per_page_with_expected_parts():
    parsed = parse_email(_load("rfq-05-pdf-attachment.eml"))
    bundle = build_source_bundle(parsed)
    pdf_chunks = [c for c in bundle.chunks if "page" in c.name]
    assert len(pdf_chunks) == 1  # the sample PDF is single-page

    pdf_text = pdf_chunks[0].text
    for part in ("STM32F407VGT6", "ILI9486", "AMS1117-3.3", "USB-C"):
        assert part in pdf_text


def test_pdf_sample_body_retains_addition_not_in_the_pdf():
    """DATA_FLOW.md: 'the PDF bundle retains the body-only NEO-6M
    addition' — the body chunk must survive even though the parts list
    lives in the PDF attachment."""
    parsed = parse_email(_load("rfq-05-pdf-attachment.eml"))
    bundle = build_source_bundle(parsed)
    body_chunks = [c for c in bundle.chunks if c.name == "email body"]
    assert len(body_chunks) == 1
    assert "NEO-6M" in body_chunks[0].text
    assert "100" in body_chunks[0].text


def test_headers_chunk_carries_sender_subject_and_date():
    parsed = parse_email(_load("rfq-01-bullet.eml"))
    bundle = build_source_bundle(parsed)
    headers_chunk = next(c for c in bundle.chunks if c.name == "email headers")
    assert "john.smith@acme-electronics.com" in headers_chunk.text
    assert "RFQ - Electronic Components Quote Request" in headers_chunk.text


def test_empty_input_is_rejected():
    with pytest.raises(EmailParseError):
        parse_email(b"")
    with pytest.raises(EmailParseError):
        parse_email(b"   \n  ")


def test_html_only_body_is_converted_to_text_with_a_warning():
    raw = (
        b"From: sender@example.com\r\n"
        b"Subject: Quote request\r\n"
        b"Date: Mon, 08 Jun 2026 09:14:00 +0000\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<html><body><p>Please quote <b>500</b> units of LM358N.</p>"
        b"<script>alert('x')</script></body></html>\r\n"
    )
    parsed = parse_email(raw)
    assert "<p>" not in parsed.body_text
    assert "alert" not in parsed.body_text
    assert "500" in parsed.body_text
    assert "LM358N" in parsed.body_text
    assert any("HTML" in w for w in parsed.warnings)


def test_unsupported_attachment_type_is_rejected_explicitly():
    parsed = ParsedEmail(
        sender="a@example.com",
        subject="RFQ",
        date=None,
        body_text="see attachment",
        attachments=[
            EmailAttachment(
                filename="parts.xlsx",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                content=b"not a real xlsx",
            )
        ],
    )
    with pytest.raises(UnsupportedAttachmentError):
        build_source_bundle(parsed)


def test_scanned_pdf_with_no_text_layer_is_rejected_explicitly():
    # A syntactically valid but blank single-page PDF: pypdf reads it fine
    # but extract_text() returns "" for every page, exactly like a scanned
    # image-only PDF.
    blank_pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n"
        b"trailer<</Size 4/Root 1 0 R>>\n"
        b"startxref\n0\n%%EOF"
    )
    parsed = ParsedEmail(
        sender="a@example.com",
        subject="RFQ",
        date=None,
        body_text="see attachment",
        attachments=[
            EmailAttachment(filename="scan.pdf", content_type="application/pdf", content=blank_pdf)
        ],
    )
    with pytest.raises(UnsupportedAttachmentError):
        build_source_bundle(parsed)


def test_blank_plain_text_falls_back_to_usable_html():
    msg = EmailMessage(policy=policy.default)
    msg["From"] = "a@example.com"
    msg["Subject"] = "RFQ"
    msg["Date"] = "Mon, 08 Jun 2026 09:14:00 +0000"
    msg.set_content("   \n  \n")  # whitespace-only plain part
    msg.add_alternative("<html><body><p>Please quote 50 ABC123</p></body></html>", subtype="html")

    parsed = parse_email(msg.as_bytes())
    assert "ABC123" in parsed.body_text
    assert "50" in parsed.body_text
    assert any("HTML" in w for w in parsed.warnings)


def test_html_table_cells_stay_separated():
    html = (
        "<html><body><table>"
        "<tr><th>Part</th><th>Qty</th></tr>"
        "<tr><td>ABC123</td><td>50</td></tr>"
        "</table></body></html>"
    )
    msg = EmailMessage(policy=policy.default)
    msg["From"] = "a@example.com"
    msg["Subject"] = "RFQ"
    msg["Date"] = "Mon, 08 Jun 2026 09:14:00 +0000"
    msg.set_content(html, subtype="html")

    parsed = parse_email(msg.as_bytes())
    assert "ABC12350" not in parsed.body_text
    assert "PartQty" not in parsed.body_text
    assert "ABC123" in parsed.body_text
    assert "50" in parsed.body_text


def test_forwarded_email_attachment_is_rejected_explicitly():
    inner = EmailMessage(policy=policy.default)
    inner["From"] = "buyer@example.com"
    inner["Subject"] = "Fwd: parts needed"
    inner.set_content("Please quote 500 of ABC123")

    outer = EmailMessage(policy=policy.default)
    outer["From"] = "a@example.com"
    outer["Subject"] = "RFQ - forwarded"
    outer["Date"] = "Mon, 08 Jun 2026 09:14:00 +0000"
    outer.set_content("See attached forwarded email.")
    outer.add_attachment(inner, filename="forwarded.eml")

    with pytest.raises(UnsupportedAttachmentError):
        parse_email(outer.as_bytes())


def test_image_nested_in_multipart_related_is_found_not_dropped():
    """A real attachment can sit inside a multipart/related that is itself
    just one branch of a multipart/alternative body. It must be discovered
    (and then explicitly rejected as an unsupported type), not silently
    disappear with no warning at all."""
    msg = EmailMessage(policy=policy.default)
    msg["From"] = "a@example.com"
    msg["Subject"] = "RFQ - scanned table"
    msg["Date"] = "Mon, 08 Jun 2026 09:14:00 +0000"
    msg.set_content("Please see the scanned parts list below.")
    msg.add_alternative(
        '<html><body><p>See image below</p><img src="cid:scan1"></body></html>', subtype="html"
    )
    html_part = msg.get_payload()[1]
    fake_png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    html_part.add_related(fake_png, maintype="image", subtype="png", cid="scan1", filename="scan.png")

    parsed = parse_email(msg.as_bytes())
    assert [a.filename for a in parsed.attachments] == ["scan.png"]

    with pytest.raises(UnsupportedAttachmentError):
        build_source_bundle(parsed)


def test_malformed_csv_with_unterminated_quote_is_rejected():
    parsed = ParsedEmail(
        sender="a@example.com",
        subject="RFQ",
        date=None,
        body_text="see attached",
        attachments=[
            EmailAttachment(
                filename="parts.csv",
                content_type="text/csv",
                # Unterminated quote folds two intended rows into one
                # multiline cell under non-strict parsing.
                content=b'PartNumber,Quantity\n"ABC123,50\nDEF456,75\n',
            )
        ],
    )
    with pytest.raises(UnsupportedAttachmentError):
        build_source_bundle(parsed)
