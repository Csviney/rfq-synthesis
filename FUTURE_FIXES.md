# Future fixes

Changes I'd like to make with more time, outside the current scope and time
budget.

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

## Ground triage in structured decision evidence

Have the existing model call identify which items have quantity ranges or
approximations and which stated requirements need internal sourcing review,
with supporting source text. Keep these signals in the private model envelope
and apply general workflow rules to select the category and next action.

Live reviews found inconsistent sourcing recommendations for multi-project
requests and quantity-break advice applied to fixed quantities; a subsequent
agent evaluation reported the reverse error on a genuine range. Explicit
signals would separate source requirements from normalized quantities and make
decisions easier to inspect and test. Preserve the public `/ingest` contract
and single model call. Validate on unseen and repeated cases: deterministic
routing cannot compensate for incorrectly extracted signals.

## Prioritize RFQs by expected profit and quoting effort

Extend triage beyond date checks, customer urgency, and next-action guidance
to support the underlying business objective: focus quoting effort on RFQs
most likely to generate profit. The current action categories help decide
what to do; a future commercial ranking should also help decide which
opportunity deserves attention first.

Connect purchasing costs, achievable selling prices, inventory and supplier
availability, fulfillment costs, customer history, and quote outcomes. Use
those records to estimate contribution profit and likelihood of winning,
then consider the effort required to prepare the quote. Large quantities or
high customer target prices alone are not evidence of profitability.

Calculate financial measures from business records; use the LLM to interpret
requirements and explain how the evidence affects the recommendation. Show
missing data and uncertainty, allow human overrides, and retain urgency and
customer commitments alongside commercial value. Do not invent profit or
win-probability estimates from email text alone.

Evaluate this direction against realized contribution profit, quote conversion,
response time, and quoting effort—not just agreement with a priority label.
This is a future integration and evaluation effort, not a change to the
current triage implementation.

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
