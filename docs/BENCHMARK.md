# Benchmark and Evaluation

This document records the evaluation scope, observed benchmark artifacts,
runtime measurements, limitations, and development disclosures for the
UniHack Product Intelligence MVP.

The results described here are observations from specific runs. They are not
guarantees of live-search availability, Gemini latency, catalogue-wide
accuracy, or production-scale performance.

## Evaluation scope

The source catalogue contains 1,000 catalogue rows.

During iterative development, live enrichment was intentionally evaluated on
a representative 10-row subset rather than the complete 1,000-row catalogue.
Running repeated external search and Gemini-backed enrichment across the full
catalogue would require substantially more API calls, runtime, and external
API expenditure during development.

Therefore, the live benchmark should be interpreted as an end-to-end
functional and quality evaluation of the selected 10-row subset. It does not
establish 1,000-row accuracy, coverage, or production-scale throughput.

The repository may contain larger input data, but benchmark observations and
local result artifacts should not be interpreted as evidence that all 1,000
rows were successfully enriched.

## Reference benchmark results

The latest supplied 10-row benchmark snapshot is:

| Final status | Rows | Accepted attributes |
|---|---:|---:|
| `ready` | 6 | 46 |
| `needs_review` | 4 | 0 |
| `blocked` | 0 | 0 |
| `failed` | 0 | 0 |
| **Total** | **10** | **46** |

Row-level results from that supplied artifact:

| MPN | Status | Accepted attributes |
|---|---|---:|
| `DCB518ASTS06G` | `ready` | 4 |
| `3MABR-7100075678` | `needs_review` | 0 |
| `5B-332-080` | `ready` | 8 |
| `49-94-0013` | `ready` | 8 |
| `543302146` | `ready` | 8 |
| `ADCB15516BS` | `needs_review` | 0 |
| `1513724` | `needs_review` | 0 |
| `1517603` | `needs_review` | 0 |
| `576512` | `ready` | 9 |
| `578808` | `ready` | 9 |

`needs_review` is not a bad outcome. It is an intentional safety state for
products where the system found useful evidence but could not safely resolve
an identity, source, conflict, validation issue, or another decision required
for automatic delivery. It protects the catalogue from unsupported guesses
and gives a reviewer a focused diagnostic to resolve. A high-quality pipeline
should prefer a trustworthy review outcome over an unsafe `ready` result.

`review.csv` contains the row-level explanations for these outcomes, while
`candidate_telemetry.csv` records bounded candidate decisions, including
candidates that were skipped, rejected, not fetched, not verified,
conflicted, or successfully verified. These files describe this benchmark
snapshot; future live runs may differ because search, retrieval, and Gemini
providers are external services.

## Runtime measurement

The application includes bounded runtime diagnostics for the major execution
stages:

- Serper discovery searches;
- domain-constrained searches;
- source retrieval;
- product identification;
- attribute extraction;
- validation and delivery mapping.

It also supports per-search diagnostics containing:

- MPN;
- query;
- query type;
- duration;
- result count;
- safe error category.

The diagnostics deliberately do not record:

- API keys;
- credentials;
- prompts;
- raw provider responses;
- raw provider exception payloads.

Runtime is dominated by external network and model-provider operations rather
than local Python computation. Consequently, observed elapsed time can vary
substantially between runs.

### Latest benchmark timing

The latest supplied `runtime_diagnostics (5).csv` reports a total batch time
of **511.33 seconds**, or approximately **8 minutes 31 seconds**, for the
10-row run.

| Instrumented phase | Aggregate time | Calls | Approximate share of total* |
|---|---:|---:|---:|
| Serper searches | 88.19 s | 66 | 17.2% |
| Domain-constrained searches | 36.07 s | 28 | included in Serper |
| Source retrieval | 23.72 s | 12 | 4.6% |
| Product identification | 84.89 s | 6 | 16.6% |
| Attribute extraction | 349.89 s | 6 | 68.4% |
| Validation/delivery mapping | 0.014 s | — | negligible |

\*The percentages are directional. Search timing is accumulated across
individual requests, and domain-constrained time is a subset of search time;
instrumented phase totals therefore should not be added as independent wall
clock intervals. Parallel search can also make aggregate request time exceed
the elapsed batch time.

The largest measured contributor was Gemini attribute extraction: 349.89
aggregate seconds across six requests, approximately 58.3 seconds per call.
Product identification was the next significant model-provider phase at
84.89 seconds. Search was also material at 66 requests, while local validation
and delivery mapping were effectively zero-cost by comparison.

External factors that can affect this runtime include Serper response latency,
DNS/TLS and page-server latency, HTTP throttling, Gemini model queueing,
provider quota/rate limits, transient 503 retries and backoff, network path
quality, and variation in returned page size or model response time. These
factors can change between identical runs without any code change.

The deterministic automated test suite is separate from enrichment runtime.
Its pass count and warnings must be taken from the actual test invocation;
documentation does not pin a historical count because the suite changes as
the branch evolves.

This is test-suite runtime, not catalogue enrichment runtime.

## Runtime optimization experiments

Runtime optimization was evaluated during development rather than assuming
that every reduction in wall-clock time represented an improvement.

### Bounded Serper parallelism

Independent Serper discovery searches were parallelized with a bounded
concurrency of three workers.

The implementation preserves:

- generated query content;
- query deduplication;
- initial/domain-constrained query distinction;
- deterministic query ordering;
- candidate ordering;
- candidate scheduling;
- exact-MPN verification;
- identity verification;
- retailer rejection;
- governance rules;
- final status behavior.

A sequential configuration remains available for comparison.

### Gemini attribute extraction

Bounded parallel Gemini attribute extraction was also experimentally tested.

The parallel configuration produced a substantial reduction in observed
runtime in a live experiment. However, it also produced fewer successfully
enriched attributes in that run.

The implementation analysis did not establish a safe result-merging failure.
The most plausible causes were provider/client concurrency behavior, including
possible API rate limiting or shared SDK-client concurrency limitations.

Because trustworthy enrichment is more important than reducing latency, the
parallel Gemini configuration was not retained as the default.

The current safe configuration therefore keeps Gemini attribute extraction
sequential while retaining bounded Serper parallelism.

This is an intentional quality-over-latency trade-off.

### Optimizations already implemented

The project has already reduced avoidable runtime while preserving the trust
model by:

- parallelizing independent Serper discovery searches with a bounded pool of
  three workers;
- preserving original query order and deterministic candidate processing
  after parallel search;
- deduplicating generated queries and candidate URLs/domains;
- using bounded candidate retrieval attempts and per-domain scheduling;
- retaining sequential Gemini attribute extraction as the safe default after
  the parallel experiment reduced enrichment quality;
- adding runtime, per-search, and candidate telemetry so future changes can
  be measured rather than judged only by wall-clock time.

### Safe future improvements

The safest next candidates for reducing runtime are:

1. Add per-row timing and extraction outcome telemetry so slow or failed
   Gemini calls can be separated from provider variability.
2. Reuse verified search/retrieval results within one run where the URL,
   MPN, policy, and fetched content are identical. Cache entries must retain
   provenance and must not cross trust-policy boundaries.
3. Remove only demonstrably redundant discovery queries after measuring their
   recall contribution. Query removal must be evaluated against missed-source
   cases, not just elapsed time.
4. Consider bounded parallel source retrieval with deterministic merge order,
   but only after measuring server throttling and preserving the existing
   global attempt limit.
5. Investigate Gemini request latency and provider quotas before considering
   any Gemini concurrency. The previous parallel extraction experiment is not
   a safe default because it produced fewer successful extracted results.

Potentially faster approaches such as unrestricted concurrency, skipping
search variants, removing retries, or weakening verification are not
acceptable optimizations because they can reduce recall, reproducibility,
provider reliability, or evidence quality.

## Current performance position

The current runtime should be understood as the runtime of the safe,
quality-preserving configuration rather than the theoretical minimum of the
pipeline.

There are additional possible optimizations, including further bounded
parallelism for independent network operations, caching, or reducing
unnecessary external requests. These were not adopted solely on the basis of
potential speed improvements because they could affect:

- source-discovery recall;
- candidate ordering;
- provider rate limits;
- reproducibility;
- enrichment quality;
- verification behavior;
- review/status outcomes.

An optimization is not considered successful if it reduces runtime by
silently reducing trustworthy enrichment.

## Quality and governance

The pipeline intentionally fails closed.

Search results, snippets, rankings, retailer pages, and unverified candidate
URLs are treated as discovery information rather than product evidence.

A source must pass the applicable policy and retrieval checks, exact MPN
verification, and identity verification before it becomes an enrichment
source.

Accepted attribute values must satisfy the existing evidence and validation
rules. Unsupported, conflicting, or insufficiently evidenced values remain
visible through diagnostics or review rather than being filled through
unsupported inference.

The delivery gate therefore prioritizes trustworthy output over apparent
column coverage.

## Delivery coverage

The delivery schema contains exactly 252 ordered columns. Normal application
execution loads the canonical header from
`data/unihack_delivery_schema.csv`; external expected-output headers are only
used when an evaluation or fixture explicitly requests them.

The mapper already supports a broad range of fields, including:

- manufacturer and brand information;
- product descriptions;
- validated attributes;
- explicit feature lists;
- identifiers;
- dimensions and measurements;
- selling quantity and UOM;
- packaging;
- warranty;
- images;
- references;
- classified documents.

Recent development also added bounded page-local structured metadata
extraction for explicit information such as:

- GTIN variants;
- MPN/SKU;
- brand;
- dimensions;
- weight;
- volume;
- selling/package quantity;
- packaging;
- warranty.

This does not imply that these fields will be populated for every product.
They are populated only when the verified source explicitly exposes compatible
structured information and the existing validation and UOM rules permit the
value to be accepted.

The system intentionally does not infer missing commercial, dimensional,
identifier, or descriptive values merely to increase the number of populated
delivery columns.

## External-provider variability

Live enrichment depends on external search and Gemini services.

Different runs can therefore produce different:

- search candidates;
- source availability;
- HTTP/retrieval outcomes;
- Gemini responses;
- provider errors;
- accepted attribute counts;
- review statuses;
- elapsed runtimes.

Provider quota, transient availability, network conditions, and search-result
variation can all affect an individual run.

A single live run should therefore not be treated as a statistical estimate
of full-catalogue performance.

## Known evaluation limitations

The following limitations should be considered when interpreting the results:

1. The complete 1,000-row catalogue was not live-enriched during development.
2. The primary live evaluation used a 10-row subset.
3. The benchmark is dependent on external search and Gemini provider behavior.
4. Runtime measurements are not guaranteed to be stable between runs.
5. Search and model responses can vary even when the application code and
   inputs remain unchanged.
6. The benchmark does not establish production-scale throughput.
7. The benchmark does not establish full-catalogue accuracy or coverage.
8. The checked-in CSV artifacts represent particular runs rather than
   continuously updated live results.

These limitations are intentional disclosures rather than hidden assumptions.

## AI-assisted development disclosure

AI coding tools were used during development of this project.

They were used for activities including:

- implementation assistance;
- code generation;
- debugging;
- test generation;
- refactoring;
- investigation of implementation issues;
- performance analysis;
- documentation drafting and refinement.

AI-generated suggestions were reviewed and iterated during development and
were validated against the project's automated tests and observed runtime
behavior.

The project does not claim that all implementation or documentation was
written manually without AI assistance.

The evidence, verification, validation, governance, delivery, and
fail-closed rules described by this repository are implemented within the
project itself and are not treated as automatically trustworthy merely
because an AI tool suggested an implementation.

## How to interpret the benchmark

The benchmark is intended to demonstrate:

- end-to-end catalogue enrichment;
- governed source discovery;
- exact-MPN verification;
- identity verification;
- evidence-backed extraction;
- deterministic validation;
- review and blocking behavior;
- safely gated 252-column delivery;
- diagnostic and telemetry capabilities.

It is not intended to claim:

- complete enrichment of all 1,000 input rows;
- guaranteed live-provider availability;
- fixed runtime across executions;
- automatic completion of unsupported fields;
- perfect source discovery or attribute coverage.

The system deliberately prefers a smaller set of trustworthy, evidence-backed
values over a larger set of unsupported or inferred values.
