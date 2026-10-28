"""RankingAgent — orders scored candidates, best first. Pure Python, no LLM.

Rules:
  - Sort by `match_score`, highest first.
  - Ties are broken by `skill_match`, then `semantic_match`, then `candidate_id`,
    so the order never depends on the order rows happened to come back from the
    database. (The tie-break is deliberately never a name.)
  - Candidates with no usable score are listed last with `rank: None`, rather
    than being treated as a 0 — "not scored" is different from "scored zero".
  - Equal scores share a rank (1, 2, 2, 4).
  - Input dicts are never mutated; each result is a copy with a `rank` added.
"""

from typing import Any


class RankingAgent:
    """Usage:
    ranked = RankingAgent().rank(candidates)            # all, best first
    top10 = RankingAgent().rank(candidates, top_n=10)
    """

    def rank(
        self, candidates: list[dict], *, top_n: int | None = None
    ) -> list[dict[str, Any]]:
        entries = [dict(c) for c in candidates or [] if isinstance(c, dict)]

        scored = [e for e in entries if self._score(e) is not None]
        unscored = [e for e in entries if self._score(e) is None]

        scored.sort(key=self._sort_key)
        unscored.sort(key=lambda e: str(e.get("candidate_id") or ""))

        previous_score: float | None = None
        previous_rank = 0
        for index, entry in enumerate(scored, start=1):
            score = self._score(entry)
            entry["rank"] = previous_rank if score == previous_score else index
            previous_score, previous_rank = score, entry["rank"]
        for entry in unscored:
            entry["rank"] = None

        ranked = scored + unscored
        return ranked[:top_n] if top_n is not None and top_n >= 0 else ranked

    # ── Helpers ────────────────────────────────────────────────────────────

    @classmethod
    def _sort_key(cls, entry: dict) -> tuple:
        return (
            -cls._score(entry),
            -(cls._number(entry.get("skill_match")) or 0.0),
            -(cls._number(entry.get("semantic_match")) or 0.0),
            str(entry.get("candidate_id") or ""),
        )

    @classmethod
    def _score(cls, entry: dict) -> float | None:
        return cls._number(entry.get("match_score"))

    @staticmethod
    def _number(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if value != value:  # NaN
            return None
        return float(value)
