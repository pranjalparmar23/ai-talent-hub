from typing import List


class RankingAgent:
    def rank(self, candidates: List[dict]) -> List[dict]:
        """Sort candidates by composite match score descending."""
        return sorted(candidates, key=lambda c: c.get("match_score", 0), reverse=True)
