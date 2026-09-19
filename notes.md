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