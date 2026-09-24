"""Parsed input, the source bundle handed to the model, and the public
output contract with its private provider envelope."""

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
ConfidenceField = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
PriceField = Annotated[float, Field(ge=0, allow_inf_nan=False)]

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_exact_iso_date(value: object) -> date:
    """Require model-produced dates to use YYYY-MM-DD and represent a real
    calendar date. Reject other formats instead of silently converting them."""
    if not isinstance(value, str) or not _DATE_PATTERN.match(value):
        raise ValueError('dueDate must be an exact "YYYY-MM-DD" string')
    return date.fromisoformat(value)


# The before-validator converts a valid YYYY-MM-DD string into a date.
# Pydantic's strict validation then confirms it received a date object.
DueDateField = Annotated[date, BeforeValidator(_parse_exact_iso_date)]


def _require_actual_bool_is_rfq(data: object) -> object:
    """Require isRfq to be an actual boolean, rejecting numbers and strings."""
    if isinstance(data, dict) and "isRfq" in data and not isinstance(data["isRfq"], bool):
        raise ValueError("isRfq must be a boolean")
    return data


@dataclass
class EmailAttachment:
    """A decoded MIME attachment. Binary content only — interpreting it
    (CSV rows, PDF pages, or rejecting it) is attachment_parser.py's job."""

    filename: str
    content_type: str
    content: bytes


@dataclass
class ParsedEmail:
    """Regular parse of one raw email."""

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


PublicResult = Union[RfqResult, NonRfqResult]

TriageCategory = Literal["begin_pricing", "review_sourcing", "clarify_with_customer"]

TRIAGE_CATEGORY_LABELS: dict[str, str] = {
    "begin_pricing": "Begin pricing",
    "review_sourcing": "Review sourcing requirements",
    "clarify_with_customer": "Clarify with customer",
}


class TriageRecommendation(BaseModel):
    """An advisory next action for the quoting employee, generated
    alongside extraction. Internal only — never part of the public
    RfqResult/NonRfqResult contract."""

    model_config = ConfigDict(extra="forbid", strict=True)

    category: TriageCategory
    reason: str = Field(min_length=1)
    nextStep: str = Field(min_length=1)

    @field_validator("reason", "nextStep")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if value.strip() == "":
            raise ValueError("must not be blank")
        return value


class ModelEnvelope(BaseModel):
    """Private provider-only wrapper. Never exposed by /ingest directly;
    the extractor returns envelope.result. triage is required for an RFQ
    and must be null otherwise."""

    model_config = ConfigDict(extra="forbid", strict=True)

    result: PublicResult
    triage: TriageRecommendation | None

    @model_validator(mode="after")
    def _require_triage_iff_rfq(self) -> "ModelEnvelope":
        if isinstance(self.result, RfqResult) and self.triage is None:
            raise ValueError("triage is required when result is an RFQ")
        if isinstance(self.result, NonRfqResult) and self.triage is not None:
            raise ValueError("triage must be null when result is not an RFQ")
        return self
