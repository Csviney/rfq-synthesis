# Future fixes

Changes I'd like to make with more time, outside the current scope and time
budget. Not part of the agreed architecture in `architecture/`; nothing
here should be implemented without separately deciding to expand scope.

## Compare model quality and cost before scaling

For the MVP, use the configured model and the agreed sample acceptance
checks. This is a provisional choice, not evidence that it provides the
best accuracy for the cost. Do not add a benchmarking framework or runtime
cost-tracking infrastructure to the current build.

Before production deployment, evaluate candidate models on the same held-out
RFQ set with consistent prompts and scoring. Measure classification accuracy,
part/quantity pairing, missing and invented fields, ambiguity handling,
injection resistance, and failures. Include correct JSON null handling:
Claude reports that gpt-4o-mini still sometimes returns the string "null"
for an unstated manufacturer after a prompt clarification; this is a semantic
extraction error even though the value passes the string-or-null schema.

Record model/version, prompt version, input/output token usage, latency,
and estimated request cost using dated provider prices. Compare quality
against cost per email and projected workload cost, accounting for failed
requests and repeat-run variability. Choose the least costly model that
meets the agreed quality threshold rather than optimizing token price alone;
small differences in error rate and cost compound at production volume.

## Warning dedup is exact-string-match only

`app/rfq_service.py::_finalize_rfq` merges model-produced warnings with
service-added warnings (parser warnings, the due-date-before-email-date
flag, the empty-lineItems flag) via `dict.fromkeys(...)`, which only
collapses two warnings that are byte-for-byte identical strings.

Two warnings that mean the same thing but are worded differently both
survive. Concretely: if the model already flags "No parts could be
identified" on an RFQ with zero line items, and the service then
unconditionally appends "RFQ has no identifiable line items", both show up
in the response — a human sees two warnings for one issue.

This is currently an accepted tradeoff (never silently drop a signal, and
true semantic dedup would need another model call to judge similarity,
which isn't worth the cost/complexity for this baseline). With more time:
consider either (a) telling the model in the prompt not to self-report the
specific conditions the service already guarantees deterministically (empty
line items, due-date-vs-email-date), so there's only ever one source per
condition, or (b) a light similarity check (e.g. normalized substring/token
overlap) before falling back to appending both.
