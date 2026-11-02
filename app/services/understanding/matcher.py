# app/services/understanding/matcher.py
"""
Semantic matcher: propose a canonical concept for each source column.

Two implementations behind one Protocol:
  - DeterministicMatcher (this file): exact + fuzzy matching. No LLM.
  - LLMMatcher (Step 9): LLM-assisted proposals for ambiguous columns.

The service layer picks which matcher to use; nothing else cares.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from rapidfuzz import fuzz

from app.db.models.semantic import CanonicalConcept

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def normalize_column_name(name: str) -> str:
    """
    'Customer Name' -> 'customername'
    'customer_name' -> 'customername'
    'customerName'  -> 'customername'
    'CUST_NM'       -> 'custnm'
    """
    s = _CAMEL_BOUNDARY.sub("_", name)
    s = s.lower()
    s = _NON_ALNUM.sub("", s)
    return s


def _normalized_parts(name: str) -> set[str]:
    """Return normalized components from a column name."""
    camel_split = _CAMEL_BOUNDARY.sub("_", name)
    return {
        normalize_column_name(part)
        for part in re.split(r"[^A-Za-z0-9]+", camel_split)
        if part
    }


@dataclass(frozen=True)
class MappingProposal:
    source_column: str
    canonical_concept_key: str
    confidence: float
    rationale: dict[str, object]


class SemanticMatcher(Protocol):
    async def propose(
        self,
        *,
        columns: list[str],
        concepts: list[CanonicalConcept],
    ) -> list[MappingProposal]: ...


_EXACT = 100.0
_STRONG = 90.0
_WEAK = 75.0

# A candidate that equals one whitespace/underscore-delimited part of the
# column name is a component match, not a full-name match, and must not be
# scored as _EXACT. It sits just below a full fuzzy match so that a genuine
# whole-name match always wins, while still beating weak fuzzy noise.
_COMPONENT = _STRONG

# Short candidate strings are dangerous: 'unit' and 'id' are substrings of
# many unrelated column names, and 'unit' is itself a synonym of some
# BusinessUnit concepts. A candidate this many characters or shorter only
# counts when it matches the column name exactly.
_COMPONENT_MIN_LEN = 4


def _score_match(
    norm: str,
    parts: set[str],
    cand: str,
    cand_parts: frozenset[str],
) -> float:
    """
    Score one column name against one candidate (both normalized).

    A whole-name match — exact or fuzzy — always outranks a match that only
    lines up one word, and short candidates only ever match exactly.
    """
    if norm == cand:
        return _EXACT

    whole = float(fuzz.ratio(norm, cand))
    is_part = cand in parts or cand_parts <= parts

    # A short candidate may only ever match the entire column name. Letting
    # 'unit' (4 chars) fuzzy-compare against 'unit_price' both loses the true
    # match and produces noise ('unit' scores 90 against 'unitprice').
    if len(cand) <= _COMPONENT_MIN_LEN:
        return 0.0

    if is_part:
        # Partial credit: at least _COMPONENT, but a strong fuzzy whole-name
        # score is kept if it is higher.
        return max(_COMPONENT, whole)

    return whole


class DeterministicMatcher:
    """
    Deterministic matcher: exact, then fuzzy, then give up.

    Priority (highest score wins):
      1. _EXACT      the column name equals a synonym/display name in full
                     ('unit price' == 'unit price').
      2. fuzzy 90+   close whole-name match ('suppliers' ~ 'supplier').
      3. _COMPONENT  the candidate equals one part of a multi-word column
                     ('price' in 'unit_price'), or a short candidate that
                     matches the whole name.
      4. fuzzy 75+   weak whole-name match.

    Matched-only-as-one-part is deliberately weaker than a whole-name
    match, and very short candidates ('unit', 'id') are restricted to
    whole-name equality. Without this, 'unit_price' scored 100 against a
    BusinessUnit concept whose synonym is 'unit', beating the concept with
    the true synonym 'unit_price'.

    Each column maps to at most one concept — the highest-scoring one.
    """

    async def propose(
        self,
        *,
        columns: list[str],
        concepts: list[CanonicalConcept],
    ) -> list[MappingProposal]:
        proposals: list[MappingProposal] = []

        # A candidate is matched both as a normalized string and as a set of
        # its own parts, so 'unit price' can match 'unit_price' as a whole
        # rather than only via its synonym list.
        concept_candidates: list[tuple[CanonicalConcept, list[tuple[str, frozenset[str]]]]] = []
        for c in concepts:
            raw_candidates = [c.display_name, *(c.synonyms or [])]
            seen: set[str] = set()
            deduped: list[tuple[str, frozenset[str]]] = []
            for raw in raw_candidates:
                cand = normalize_column_name(raw)
                if not cand or cand in seen:
                    continue
                seen.add(cand)
                deduped.append((cand, frozenset(_normalized_parts(raw))))
            concept_candidates.append((c, deduped))

        for col in columns:
            norm = normalize_column_name(col)
            if not norm:
                continue
            exact_parts = _normalized_parts(col)

            best_score: float = 0.0
            best_concept: CanonicalConcept | None = None
            best_matched: str | None = None

            for concept, candidates in concept_candidates:
                for cand, cand_parts in candidates:
                    score = _score_match(norm, exact_parts, cand, cand_parts)
                    if score > best_score:
                        best_score = score
                        best_concept = concept
                        best_matched = cand

            if best_concept is None or best_matched is None:
                continue
            if best_score < _WEAK:
                continue

            if best_score == _EXACT:
                method = "exact"
            elif best_score >= _STRONG:
                method = "fuzzy_strong"
            else:
                method = "fuzzy_weak"

            proposals.append(MappingProposal(
                source_column=col,
                canonical_concept_key=best_concept.key,
                confidence=round(best_score / 100.0, 4),
                rationale={
                    "method": method,
                    "matched": best_matched,
                    "score": round(best_score, 2),
                },
            ))

        return proposals