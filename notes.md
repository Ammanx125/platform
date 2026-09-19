Notes on the design:

'_infer_column_type' uses a 95% threshold. A column with 96% valid dates and 4% typos still infers as date. This is deliberate — real-world data is dirty and you don't want a single bad cell to poison the type. The quality module then flags the 4% as an issue.

Order of type checks matters. 'boolean' before 'integer' because True/False would otherwise pass '_try_int' if someone stored 1/0. integer before float so 42 isn't coerced to 42.0.

'_MAX_TRACKED_DISTINCT = 5000'. Beyond this we stop counting distinct values to bound memory. distinct_count = -1 signals "unknown, too many." The quality module handles the sentinel.

Duplicate row detection hashes rows as sorted key-value string tuples. This is O(n) and works on unhashable values. Not free but acceptable for a first pass.

No multi-column candidate keys. Combinatorial explosion on wide tables. Single-column keys catch the common case (an id or sku column).

high_missingness has two severity tiers: 20% warning, 50% error. This is arbitrary but a useful default. Configurable later.

impossible_quantity is heuristic on the column name. It only fires if the value is actually negative and the column looks quantity-like. It doesn't fire on balance or delta columns even if negative — those are legitimately negative.

invalid_date re-implements the date-parse check rather than importing from the profiler. Duplication is deliberate: the profiler's inference and the quality check have different jobs and might diverge (e.g. the profiler gets stricter over time, the quality check stays looser). Cross-module function imports would couple them.

inconsistent_identifier compares values that normalize to the same key. Uses _norm_key (lowercase + collapse whitespace). It does not strip punctuation — ACME-1 and ACME1 stay distinct on purpose.

duplicate_entity fires when a column looks like an id (id, *_id, sku, code) but isn't in candidate_keys.

CanonicalConcept has no tenant_id — it's a global catalog, per our earlier decision.

SemanticMapping uses TenantMixin — mappings belong to a tenant.

The unique constraint is (source_id, source_column), not (source_id, source_column, canonical_concept_key). A single source column maps to one concept. If you want to allow multiple concepts per column (e.g. order_date → both Purchase.date and Shipment.date), tell me now — it's a schema change.

rationale JSONB — records why the matcher chose this concept. Example: {"method": "exact_synonym", "matched_synonym": "supplier", "score": 1.0}. This is the Step 5 "lineage" requirement. Without it, human reviewers can't tell whether a fuzzy match was reasonable.

31 domain concepts + 4 generic attributes = 35 rows. The 31 is your table; the 4 generics are the ones that would otherwise be unmappable (date, amount, quantity, name) — they're not in your 31 because they're too generic to be a business domain concept, but they're needed as mapping targets.

kind and value_type are separate. kind is about mapping cardinality (attribute, entity, fact); value_type is about the data type (string, number, date, boolean, entity). An entity concept has kind=entity and value_type=entity. A Finance.Invoice has kind=fact and value_type=string (the invoice number is a string).

Synonyms are lowercase, no punctuation. The matcher normalizes column names the same way before comparing.

seed_canonical_concepts never deletes. If you remove a concept from the file, it stays in the DB. This is deliberate — mappings referencing it would otherwise break. Deletion is a manual operation, done via SQL or a future admin endpoint.

Normalization strips everything non-alphanumeric. customer_name, Customer Name, customerName, and CUSTOMER-NAME all become customername. This is how the matcher handles the varied naming customers actually use.

rapidfuzz.fuzz.ratio returns 0–100. _STRONG = 90 and _WEAK = 75 are heuristics; tune later based on real customer data.

A column maps to at most one concept. The highest-scoring one wins. Real datasets sometimes have unit_price that could be SellingPrice or PurchasePrice — the matcher will pick whichever concept's synonyms are closer. This is exactly why human confirmation exists.

rationale records the matched candidate and score. This is the audit trail.

Re-running propose_mappings is idempotent in effect: columns already mapped (in any status) are skipped. This means a human who confirmed a mapping won't see it re-proposed as proposed.

No auto-confirmation, ever. Even exact matches are status="proposed" and need a human to confirm. This is deliberate — see blueprint section 11: "Human confirmation: uncertain mappings become review items." Even high-confidence matches become review items because the cost of a wrong auto-confirmed mapping is a silently wrong decision downstream.

The matcher is injectable. Tests pass a FakeMatcher. Production passes DeterministicMatcher() (which is the default). Step 9 will register LLMMatcher and pick based on config.

The propose endpoint is POST /datasets/{id}/mappings/propose?job_id=... — the job_id is a query param because the endpoint operates on a job's profile but lives under a dataset route. Slightly awkward; an alternative is POST /jobs/{job_id}/mappings/propose. If you prefer the latter, say so and I'll restructure. I chose this because the natural reading is "give me mappings for this dataset."

create_mapping sets status="confirmed" directly. A human who types a mapping by hand is confirming it. This is different from matcher proposals, which start as proposed.

The unique constraint (source_id, source_column) means create_mapping returns 409 if the column already has a mapping. The right move is PATCH. This is a bit strict — some teams prefer upsert. Tell me if you want upsert semantics instead.