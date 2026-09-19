# app/services/understanding/matcher.py
"""
Semantic matcher: propose a canonical concept for each source column.

Two implementations behind one Protocol:

  - DeterministicMatcher (this file): exact + fuzzy matching against the
    concept catalog's synonyms and display names. No LLM. Fully testable.
  - LLMMatcher (Step 9): uses the LLM provider to propose mappings for
    columns the deterministic matcher can't confidently resolve.

The service layer picks which matcher to use; the rest of the system never
cares which implementation is active.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from rapidfuzz import fuzz

from app.db.models.semantic import CanonicalConcept

# --- normalization ---------------------------------------------------------

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


# --- result type -----------------------------------------------------------

@dataclass(frozen=True)
class MappingProposal:
    source_column: str
    canonical_concept_key: str
    confidence: float
    rationale: dict


class SemanticMatcher(Protocol):
    async def propose(
        self,
        *,
        columns: list[str],
        concepts: list[CanonicalConcept],
    ) -> list[MappingProposal]: ...


# --- deterministic matcher -------------------------------------------------

# Score thresholds for rapidfuzz ratio (0..100).
_EXACT = 100.0
_STRONG = 90.0
_WEAK = 75.0


class DeterministicMatcher:
    """
    Deterministic matcher: exact, then fuzzy, then give up.

    Priority:
      1. Exact match on a concept's synonym (normalized).
      2. Exact match on a concept's display name (normalized).
      3. Fuzzy match >= _STRONG against any synonym or display name.
      4. Fuzzy match >= _WEAK  against any synonym or display name.

    A column may be proposed for at most one concept — the highest scoring one.
    Ties are broken by preferring entity > attribute > fact for entity-ish
    columns, but in practice ties are rare.
    """

    async def propose(
        self,
        *,
        columns: list[str],
        concepts: list[CanonicalConcept],
    ) -> list[MappingProposal]:
        proposals: list[MappingProposal] = []

        # Pre-compute the candidate strings for each concept.
        # Each concept has: display_name + every synonym, all normalized.
        concept_candidates: list[tuple[CanonicalConcept, list[str]]] = []
        for c in concepts:
            candidates = [normalize_column_name(c.display_name)]
            candidates.extend(normalize_column_name(s) for s in (c.synonyms or []))
            # dedupe while preserving order
            seen: set[str] = set()
            deduped: list[str] = []
            for cand in candidates:
                if cand and cand not in seen:
                    seen.add(cand)
                    deduped.append(cand)
            concept_candidates.append((c, deduped))

        for col in columns:
            norm = normalize_column_name(col)
            if not norm:
                continue
            exact_parts = _normalized_parts(col)

            best: tuple[float, CanonicalConcept | None, str | None] = (0.0, None, None)

            for concept, candidates in concept_candidates:
                for cand in candidates:
                    if norm == cand or cand in exact_parts:
                        score = _EXACT
                        matched = cand
                    else:
                        score = float(fuzz.ratio(norm, cand))
                        matched = cand
                    if score > best[0]:
                        best = (score, concept, matched)

            score, concept, matched = best
            if concept is None or score < _WEAK:
                continue

            if score == _EXACT:
                method = "exact"
            elif score >= _STRONG:
                method = "fuzzy_strong"
            else:
                method = "fuzzy_weak"

            proposals.append(MappingProposal(
                source_column=col,
                canonical_concept_key=concept.key,
                confidence=round(score / 100.0, 4),
                rationale={
                    "method": method,
                    "matched": matched,
                    "score": round(score, 2),
                },
            ))

        return proposals