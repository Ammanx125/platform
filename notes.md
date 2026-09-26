# Engineering Notes

Design decisions, invariants, and known limits across the platform. These notes explain why the implementation behaves as it does and identify constraints that matter when extending it.

## Data Profiling and Quality

- **Type inference tolerates dirty data.** `_infer_column_type` accepts a type when at least 95% of values match it. For example, 96% valid dates and 4% typos still infer as dates; the quality checks report the bad values separately.
- **Type-check order is significant.** Check booleans before integers, then integers before floats. This avoids treating booleans as `1`/`0` and keeps whole numbers from being coerced to floats.
- **Distinct-value tracking is bounded.** `_MAX_TRACKED_DISTINCT` is 5,000. Above that, tracking stops and `distinct_count = -1` means “unknown; limit exceeded.” Quality checks understand this sentinel.
- **Duplicate-row detection is linear.** Rows are hashed from sorted key/value string tuples. This works with unhashable values and costs O(n), which is acceptable for the current profiling pass.
- **Candidate keys are single-column only.** Composite-key discovery is intentionally excluded to avoid combinatorial work on wide tables; common identifiers such as IDs and SKUs are covered.
- **Missingness severity defaults** to warning at 20% and error at 50%. These thresholds are heuristic and may become configurable later.
- **Negative quantity checks use column names as a hint.** `impossible_quantity` flags negative values only when the column looks quantity-like. Names suggesting balances or deltas are excluded because negative values may be valid.
- **Date quality checks are intentionally decoupled from inference.** `invalid_date` performs its own parse check instead of importing the profiler’s helper. Inference and quality have different responsibilities and may evolve independently.
- **Identifier normalization differs by purpose.** `inconsistent_identifier` lowercases and collapses whitespace but preserves punctuation, so `ACME-1` and `ACME1` remain distinct. Concept matching removes non-alphanumeric characters, so `customer_name`, `Customer Name`, `customerName`, and `CUSTOMER-NAME` normalize to the same key.
- **Duplicate-entity checks** target identifier-like columns (`id`, `*_id`, `sku`, `code`) that are not already candidate keys.

## Concepts and Semantic Mapping

- **Canonical concepts are global.** `CanonicalConcept` has no `tenant_id`; `SemanticMapping` is tenant-owned through `TenantMixin`.
- **Concept kind and value type are separate.** `kind` describes mapping cardinality (`attribute`, `entity`, `fact`); `value_type` describes data (`string`, `number`, `date`, `boolean`, `entity`). For example, an invoice number can be a `fact` with a `string` value type.
- **The base catalog has 31 domain concepts and four generic attributes** (`date`, `amount`, `quantity`, `name`) as mapping targets, for 35 entries total.
- **Synonyms are stored lowercase without punctuation.** Column names are normalized before matching. `rapidfuzz.fuzz.ratio` returns 0–100; `_STRONG = 90` and `_WEAK = 75` are starting heuristics to tune against real data.
- **One source column maps to one concept.** The unique constraint is `(source_id, source_column)`. If a column already has a mapping, `create_mapping` returns 409; update it with PATCH rather than creating a second mapping.
- **The best-scoring concept wins, but never auto-confirms.** A column such as `unit_price` may match multiple concepts, so human confirmation is required even for exact or high-confidence matches. Matcher proposals remain `proposed`; hand-created mappings are `confirmed`.
- **Mapping rationale is an audit trail.** JSONB `rationale` records how a concept was selected, such as `{"method": "exact_synonym", "matched_synonym": "supplier", "score": 1.0}`.
- **Proposal generation is idempotent in effect.** Columns with an existing mapping in any status are skipped, so confirmed mappings are not proposed again.
- **Matchers are injectable.** Tests can use `FakeMatcher`; production currently uses `DeterministicMatcher`. An `LLMMatcher` can be registered and selected through configuration later.
- **Seeding is additive.** `seed_canonical_concepts` never deletes concepts removed from the seed file, because existing mappings may depend on them. Deletion is a deliberate manual or administrative operation.
- **Mapping proposal route:** `POST /datasets/{id}/mappings/propose?job_id=...`. The job ID is a query parameter because proposals use a job profile while the route is dataset-oriented.
- **Packs reference existing concepts instead of redefining them.** For example, `procurement.v1` references catalog concepts such as `Procurement.Supplier` and defines only new concepts such as `Procurement.RFQ` inline. Installation resolves both forms.

## Secrets and Encryption

- **Fernet protects stored secrets** with authenticated encryption (AES-128-CBC plus HMAC-SHA256); tampering is detected and rejected.
- **Development key fallback derives from `JWT_SECRET`.** This avoids another local environment variable. Rotating the JWT secret makes existing fallback-encrypted webhook secrets unreadable, which is acceptable in development.
- **Production refuses the fallback.** Production must use a dedicated encryption key so JWT rotation does not silently invalidate stored secrets.
- **`DecryptionError` is distinct** so callers can distinguish ciphertext that fails authentication/decryption from data that was never encrypted.

## Retrieval and Document Processing

- **Embedding dimensions are a schema constraint.** Changing `embedding_dimensions` after migration requires a new migration (for example, `ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(N)`) and invalidates existing embeddings, which must be regenerated.
- **Retrieval uses per-result-set min-max normalization.** For example, raw vector scores `[0.9, 0.85, 0.7]` become `[1.0, 0.75, 0.0]`. The weakest item maps to zero even if its raw score was strong; this is predictable but aggressive.
- **Candidate retrieval uses a 3x multiplier.** To return 10 results, fetch 30 from each retriever before merging. This reduces the chance that the final list is dominated by one retrieval strategy.
- **Duplicate chunks are merged.** A chunk found by vector and keyword retrieval appears once, with both contributions represented in `score_components`.
- **Retrieval weights come from configuration** so they can be tuned without redeployment.
- **`Block` is immutable.** The chunker treats blocks as frozen input and assembles new `TextChunk` objects rather than mutating them.
- **Structural breaks guide strict chunking.** `is_structural_break()` controls where chunks may end: list items can join adjacent list items, while a heading cannot merge with the paragraph before it.
- **Whitespace normalization preserves line boundaries.** `_normalize_line_whitespace` collapses runs within a line without flattening intentional line breaks such as addresses or poetry.
- **Sentence splitting is intentionally approximate.** The current regex handles common cases; a full sentence splitter such as NLTK or spaCy remains a possible future improvement.
- **A generic `db: Any` type is a deliberate dependency tradeoff** in otherwise pure-data code. Use a `TYPE_CHECKING` import and an `"AsyncSession"` annotation if stronger typing is needed without a runtime SQLAlchemy import.

## Ingestion, Timestamps, and Files

- **`__source_id` is added to every row dictionary** as a private key. A real input column with that exact name would collide; the current naming convention is preferred over a tuple-based representation.
- **Series loading is capped at 100,000 rows.** Larger Python-side aggregation may become slow; tenants exceeding the cap should move to SQL-side aggregation.
- **Grouped series require confirmed mappings.** Rows whose source lacks a confirmed mapping for a grouping concept are skipped rather than placed in an ungrouped bucket. Values that cannot be converted by `_to_float` (which strips currency symbols and thousands separators) are skipped.
- **A `RowTimestamp` is unique per `(staged_row_id, kind)`.** Connectors select the most semantically appropriate candidate date for each kind. `kind` is `String(20)`, not a PostgreSQL enum; `TimeBasis` validates allowed values in code.
- **File observations preserve content history.** The unique key is `(tenant_id, source_id, path, content_hash)`. Modified content creates a new observation; seeing unchanged content again updates `last_seen_at` and sets status to `unchanged`.
- **Composite indexes are chosen for detector and UI access patterns.**

## Analytics and Forecasting

- **Irregular series do not use seasonality detection.** Autocorrelation assumes regular spacing and produces noise on irregular data.
- **The maximum candidate period scales with series length** (`n * 0.33`) because a series cannot reliably reveal a period longer than the observations support.
- **Forecast requests require explicit specifications.** There is no reliable heuristic for deciding which series a user intends to forecast. If the planner cannot supply the required specs, the capability refuses; the LLM router may supply them when used.
- **Anomaly and forecast series building remains bounded and mapping-driven.** See the ingestion notes for row caps, confirmed grouping mappings, and timestamp basis selection.

## LLM and Orchestration Boundaries

- **LLM responses are injectable.** Tests can use `MockLLMProvider(response=LLMResponse(...))`.
- **The mock provider is deterministic.** It accepts `system`, `messages`, and `tools` to satisfy the provider protocol, but ignores them and returns the injected response (including `raw`, which may be `None`).
- **System and messages have distinct trust levels.** `system` contains application-controlled identity, policy, safety, and output requirements. `messages` contain runtime content such as user requests, retrieved facts, documents, and prior turns. Tenant-controlled or retrieved content is data, never system instruction. Python does not enforce this against every caller, so call sites require review and tests.
- **Provider validation is structural; orchestration validation is semantic.** Provider adapters parse and validate the canonical `LLMResponse` shape, including tool-call structure. The orchestrator validates tool existence, authorization, argument meaning, tenant constraints, business rules, and approval requirements before execution. Provider adapters must not own the tool registry or business policy.
- **Capabilities do not call each other.** Retrieval and KPI are independent; the executor runs them sequentially and assembles their results.
- **`_infer_kpi_keys` and `_infer_detector_keys` are lightweight name-overlap heuristics.** They cover common queries; the LLM router handles more complex routing.

Validator = Any — typing validators strictly requires a Callable Protocol that Pydantic models can't easily satisfy across tools. Any is honest; the runtime check in the registry verifies the shape.

parameters_model is required — because the schema for the LLM comes from it. If a tool wants to provide a raw schema instead, that's what c: both was for; I'm not supporting it in 11a because nothing needs it yet. Add when a real case arrives.

execute(db, payload, context) — db is the session; payload is a validated instance of parameters_model; context carries identity. Tools that need tenant data query through db, scoped to context.tenant_id.

run_validators catches exceptions. A malformed validator doesn't crash the whole pipeline; it becomes a failed check with the exception text.

Stops at first failure. Matches the standard policy pipeline. If you'd rather collect all failures (some auditors prefer this), change break to continue. I'd keep the short-circuit — later checks may be meaningless if earlier ones fail.

_run_and_verify is shared between the auto-execute path and the approved path. Same code, same verification, same audit emission.

Audit events are emitted at every transition: proposed→pending, proposed→rejected, proposed→executed, approved→executed, approved→verification_failed, approved→failed.

verification_failed is a distinct status. It says: "we ran it, we can't confirm it happened." That's different from failed (we know it didn't run).

run_instance is idempotent on resume. It skips steps whose state is already completed. When resume_instance marks the waiting step completed, running again from the top skips everything up to the next step.

wait_for_approval=False default for run_action — per your decision G. The workflow only waits when it explicitly asks to.

await_action supports two forms of action_id_from — a bag key whose value is a list of action ids (set by a prior run_action step), or a literal UUID string. The bag form is the common case.

Multi-action waits are single-action today. A run_action step with three tool calls where two go to approval will wait only on the first. That's a documented limitation; making it wait on all of them is a change to the wait_ref model and can come later.

Two things to note:

EvidenceItem.kind = "workflow" — new kind. Update Evidence in context.py to add a workflows: list[EvidenceItem] field, and update all_items() and to_dict() accordingly. The _EVIDENCE_CAPS in executor.py needs a cap for workflow too (say, 5). And the prompt renderer should include a section for workflow evidence.

step_params.user_id — the planner doesn't currently know the user id. So the executor needs to inject it when building plan steps, or the workflow capability needs to receive it another way. Simplest: the executor, before running capabilities, adds user_id to every step's parameters if not present. I'll show that change below.