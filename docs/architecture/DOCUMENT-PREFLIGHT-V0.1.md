# Large-document relevance preflight v0.1

Checkpoint: 2026-09-13T14:01:41Z. Candidate in PR #906, not deployed.
Owner decision: inspect headings and beginnings before admitting large files to
expensive full analysis. Extends AUDIT-DEEP-RESEARCH-V0.1.md.

## Implemented policy

The verified-document path screens acquired normalized documents of at least
48,000 characters before promoting all blocks into the extraction pool. Smaller
documents bypass the classifier. This is a relevance gate, not a total LLM budget
ceiling; useful admitted documents still receive full coverage in deep mode.

1. Pass one reads title, up to 24 headings spread across the outline, and the
   first 4,000 characters. The model returns include/exclude/uncertain, confidence
   and a reason. A positive relevance decision admits the full document.
2. Before exclusion, pass two also reads middle/end excerpts and early/late
   company or registration-ID mentions. Exclusion requires both passes to agree
   with confidence at least 0.95. All other outcomes retain the full document.
3. Classifier errors, missing credentials, ambiguous content and low confidence
   never silently discard content. Each call has a 500-token output limit and
   a 15-second outer deadline; at most two calls per acquired candidate. Existing
   LLM accounting and cooperative stop apply.
4. Excluded documents remain source candidates with reason and decision, never
   promoted evidence. Reports expose the excluded count and URL/reason; uncertain
   decisions are also disclosed. Deep-run checkpoints record URL/content digest,
   sampled size, pass count and result. The existing acquisition cache is retained.
5. Discovery of official links happens before the relevance gate: an unhelpful
   index/manual cannot hide useful linked company pages. Identity verification
   remains an independent requirement before evidence promotion.

Catalogues, specifications, company technology, people, ownership, financial
reports, customers, suppliers, requisites, branches and risks are in scope.
A company name in a footer alone does not prove substantive relevance. Document
text is explicitly treated as untrusted data in the classification prompt.

## Limits and acceptance

The gate follows downloading/parsing; it saves extraction work, not network bytes
or initial parser work. Streaming/range preview for PDF/archives is not shipped.
The initial directly submitted site text retains its existing full-analysis path;
this change screens documents acquired through verification, including official
subpages. No entire-Internet completeness or zero false-exclusion claim is made.
Two sampled LLM votes are a conservative heuristic, not a formal recall guarantee.

Tests cover small-document bypass, two-pass exclusion, a useful tail rescuing an
unhelpful beginning, failure/low-confidence retention, late headings/mentions,
and exclusion before evidence promotion. Paid live classification quality and
exact-SHA stage acceptance remain open.

Local validation: 1378 passed, 1 xfailed, 6 subtests passed; git diff --check passed.
