"""Ranking helpers for retriever v2 (Phase 7, task 9).

Plain Python with no ChromaDB, so it is easy to test.

MMR (maximal marginal relevance) picks results one at a time. Each pick balances
two things: how well the chunk matches the question, and how different it is from
what was already picked. Without it, five chunks from the same page can fill the
answer.
"""

import math
from collections.abc import Hashable, Sequence


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """How alike two vectors are: 1 same direction, 0 unrelated, -1 opposite."""
    if len(a) != len(b):
        raise ValueError(f"vectors have different sizes: {len(a)} and {len(b)}")
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


def mmr_select(
    relevance: Sequence[float],
    vectors: Sequence[Sequence[float]] | None = None,
    *,
    k: int,
    lambda_mult: float = 0.7,
    groups: Sequence[Hashable] | None = None,
    max_per_group: int | None = None,
) -> list[int]:
    """Choose up to `k` candidates. Returns their positions, in the order chosen.

    relevance      how well each candidate matches the question (higher is better)
    vectors        each candidate's embedding. Without them the choice is simply the
                   best matches first
    lambda_mult    1.0 = only relevance, 0.0 = only variety (after the first pick)
    groups         a label per candidate, for example the page it came from
    max_per_group  at most this many picks from one group

    The first pick is always the best match. On equal scores the earlier candidate
    wins, so the order is steady from run to run.
    """
    n = len(relevance)
    if vectors is not None and len(vectors) != n:
        raise ValueError("vectors must have one entry per candidate")
    if groups is not None and len(groups) != n:
        raise ValueError("groups must have one entry per candidate")
    if not 0.0 <= lambda_mult <= 1.0:
        raise ValueError("lambda_mult must be between 0 and 1")
    if max_per_group is not None:
        if max_per_group < 1:
            raise ValueError("max_per_group must be at least 1")
        if groups is None:
            raise ValueError("max_per_group needs groups")
    if k <= 0 or n == 0:
        return []

    chosen: list[int] = []
    taken_per_group: dict[Hashable, int] = {}
    remaining = list(range(n))
    similarity_cache: dict[tuple[int, int], float] = {}

    def similarity(i: int, j: int) -> float:
        key = (i, j) if i < j else (j, i)
        if key not in similarity_cache:
            similarity_cache[key] = cosine_similarity(vectors[i], vectors[j])
        return similarity_cache[key]

    while remaining and len(chosen) < k:
        best: int | None = None
        best_score = 0.0
        for i in remaining:
            if max_per_group is not None:
                if taken_per_group.get(groups[i], 0) >= max_per_group:
                    continue
            if vectors is None or not chosen:
                score = relevance[i]
            else:
                redundancy = max(similarity(i, j) for j in chosen)
                score = lambda_mult * relevance[i] - (1 - lambda_mult) * redundancy
            if best is None or score > best_score:
                best, best_score = i, score
        if best is None:  # everything left is over its group limit
            break
        chosen.append(best)
        remaining.remove(best)
        if groups is not None:
            taken_per_group[groups[best]] = taken_per_group.get(groups[best], 0) + 1
    return chosen
