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

Another example, quantified during the step 6 acceptance pass:
`rfq-06-multi-project.eml` states two separate per-project delivery dates
rather than one due date. `DATA_FLOW.md` requires this to be flagged with a
warning; `app/rfq_service.py::_finalize_rfq` adds nothing for this case
itself (unlike the due-date-before-email-date and empty-lineItems warnings,
which are deterministic), so it depends entirely on the model choosing to
self-report it. Across 5 real runs of this sample (same prompt, same
input), the warning appeared in 4 and was missing in 1 — the extraction
itself (items, quantities, dates, RoHS, equivalents) was correct every
time. A ~20% miss rate on a documented requirement is a real gap to weigh
against a candidate model, not just a quirk to note.

## Manual review for low-confidence results and a correction audit trail

Before automatically accepting RFQs downstream, route low-confidence results
to an operator for inspection against the source email and attachments.
Confidence below 70% is an illustrative starting threshold, not an approved
cutoff: derive and validate the threshold from labeled evaluations and logged
review outcomes, balancing missed errors against review workload. The current
model score is self-reported and uncalibrated, not a measured probability of
correctness.

Record the original classification, extracted fields, confidence, model/prompt
version, and source reference alongside each review decision. Preserve who
reviewed it, when, the corrected classification or fields, and the reason,
rather than overwriting the original result. Use this audit trail to measure
incorrectly labeled RFQs and extraction errors, improve evaluations, and
revisit the routing threshold. Include a way to recover false negatives:
reviewing only accepted RFQs cannot discover actual RFQs mislabeled as non-RFQs.

This is a future workflow; the MVP retains its current storage, public response
contract, and confidence-independent routing.

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
