"""Parsed input, the source bundle handed to the model, and the public
output contract with its private provider envelope.

Source of truth: architecture/LLM_DESIGN.md ("Public responses" and
"Provider response") for RfqResult/NonRfqResult/ModelEnvelope, and
architecture/ARCHITECTURE.md ("Data passed between components") for
ParsedEmail/SourceBundle. Every contract model forbids extra fields and
requires every key in its branch, including keys whose value is null, so a
malformed or partial provider response fails validation instead of being
silently patched up. ParsedEmail/SourceBundle carry no such contract with
an outside caller, so they're plain dataclasses.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Annotated, Literal, Union

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

Priority = Literal["low", "medium", "high", "urgent"]

# Confidence and targetPrice are floats but a model may emit a whole number
# (e.g. `1` for full confidence) as a bare JSON integer. Pydantic's strict
# float already widens int -> float, so the model's strict=True is enough;
# no per-field strict=False, which would also silently coerce a string or a
# bool. allow_inf_nan=False rejects NaN/Infinity, which Python's json module
# accepts as numbers but the contract does not.
ConfidenceField = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
PriceField = Annotated[float, Field(ge=0, allow_inf_nan=False)]

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_exact_iso_date(value: object) -> date:
    """`date`'s lax parsing accepts more than "YYYY-MM-DD": a full
    datetime string is truncated to its date part, and an int/numeric
    string is read as a Unix timestamp (e.g. 0 -> "1970-01-01"). Require the
    exact contract format ourselves, then let date.fromisoformat reject an
    invalid calendar date (e.g. 2026-02-30)."""
    if not isinstance(value, str) or not _DATE_PATTERN.match(value):
        raise ValueError('dueDate must be an exact "YYYY-MM-DD" string')
    return date.fromisoformat(value)


# strict=True is unreachable here: the before-validator above already
# produces a real `date` object (or raises), so the built-in date validator
# just confirms the type.
DueDateField = Annotated[date, BeforeValidator(_parse_exact_iso_date)]


def _require_actual_bool_is_rfq(data: object) -> object:
    """Literal[True]/Literal[False] compare with `==`, under which
    `1 == True` and `0 == False`, so strict mode alone lets an int through.
    isRfq also doubles as the discriminator field, and pydantic forbids a
    field_validator(mode="before") there, so this runs as a whole-model
    validator instead and rejects a non-bool isRfq before field validation."""
    if isinstance(data, dict) and "isRfq" in data and not isinstance(data["isRfq"], bool):
        raise ValueError("isRfq must be a boolean")
    return data


class UnsupportedAttachmentError(ValueError):
    """Raised for an attachment this baseline cannot read: an unrecognized
    type, one that decodes but carries no extractable text (e.g. a scanned
    PDF), or one email_parser.py can't even decode to bytes (e.g. a
    forwarded message/rfc822). Unsupported/unreadable attachments fail
    explicitly rather than being silently skipped — this can also reject an
    unrelated inline logo, which is an accepted, documented tradeoff (see
    architecture/DATA_FLOW.md)."""


class NoUsableSourceTextError(ValueError):
    """Raised when parsing succeeds but leaves nothing to extract from: an
    empty body and no attachments. Sibling of UnsupportedAttachmentError —
    architecture/LLM_DESIGN.md's failure table maps both to the same 422."""


@dataclass
class EmailAttachment:
    """A decoded MIME attachment. Binary content only — interpreting it
    (CSV rows, PDF pages, or rejecting it) is attachment_parser.py's job."""

    filename: str
    content_type: str
    content: bytes


@dataclass
class ParsedEmail:
    """The deterministic parse of one raw email. No AI; standard-library
    `email` only."""

    sender: str | None
    subject: str | None
    date: datetime | None
    body_text: str
    attachments: list[EmailAttachment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class SourceChunk:
    """One named block of source text: the email headers, its body, or one
    attachment's extracted text (a CSV's rows, or one PDF page)."""

    name: str
    text: str


@dataclass
class SourceBundle:
    """All text available to the model for one email: no binary payloads,
    no interpretation of what the text means. Built by attachment_parser.py
    from a ParsedEmail."""

    chunks: list[SourceChunk] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Customer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str | None
    email: str | None
    phone: str | None
    company: str | None


class RequestDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    dueDate: DueDateField | None
    priority: Priority
    specialInstructions: str | None


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    partNumber: str = Field(min_length=1)
    manufacturer: str | None
    description: str | None
    quantity: int = Field(ge=0)
    targetPrice: PriceField | None
    notes: str | None

    @field_validator("partNumber")
    @classmethod
    def _reject_blank_part_number(cls, value: str) -> str:
        if value.strip() == "":
            raise ValueError("partNumber must not be blank")
        return value


class RfqResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    isRfq: Literal[True]
    confidence: ConfidenceField
    customer: Customer
    request: RequestDetails
    lineItems: list[LineItem]
    warnings: list[str]

    _validate_is_rfq = model_validator(mode="before")(_require_actual_bool_is_rfq)


class NonRfqResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    isRfq: Literal[False]
    confidence: ConfidenceField
    reason: str

    _validate_is_rfq = model_validator(mode="before")(_require_actual_bool_is_rfq)


# Not a Field(discriminator=...) union: pydantic renders a discriminated
# union as JSON Schema `oneOf`, which OpenAI's structured-output mode
# rejects (confirmed via the live schema smoke test in
# tests/test_live_samples.py). A plain Union renders as `anyOf`, which is
# supported; RfqResult and NonRfqResult have disjoint required keys and a
# strict, non-coercible isRfq, so member selection stays unambiguous.
PublicResult = Union[RfqResult, NonRfqResult]


class ModelEnvelope(BaseModel):
    """Private provider-only wrapper. Never exposed by /ingest directly;
    the extractor returns envelope.result."""

    model_config = ConfigDict(extra="forbid", strict=True)

    result: PublicResult
