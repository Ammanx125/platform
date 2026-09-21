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


class DeterministicMatcher:
    """
    Deterministic matcher: exact, then fuzzy, then give up.

    Priority:
      1. Exact match on a concept's synonym or display name (normalized).
      2. Fuzzy match >= _STRONG against any synonym or display name.
      3. Fuzzy match >= _WEAK  against any synonym or display name.

    Each column maps to at most one concept — the highest-scoring one.
    """

    async def propose(
        self,
        *,
        columns: list[str],
        concepts: list[CanonicalConcept],
    ) -> list[MappingProposal]:
        proposals: list[MappingProposal] = []

        # Pre-compute normalized candidate strings per concept.
        concept_candidates: list[tuple[CanonicalConcept, list[str]]] = []
        for c in concepts:
            raw_candidates = [normalize_column_name(c.display_name)]
            raw_candidates.extend(
                normalize_column_name(s) for s in (c.synonyms or [])
            )
            seen: set[str] = set()
            deduped: list[str] = []
            for cand in raw_candidates:
                if cand and cand not in seen:
                    seen.add(cand)
                    deduped.append(cand)
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
                for cand in candidates:
                    if norm == cand or cand in exact_parts:
                        score = _EXACT
                    else:
                        score = float(fuzz.ratio(norm, cand))
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