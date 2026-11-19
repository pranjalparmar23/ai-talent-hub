"""Tests for the ranking helpers (app/rag/ranking.py). Pure maths: no ChromaDB."""

import pytest

from app.rag.ranking import cosine_similarity, mmr_select

# ── cosine_similarity ────────────────────────────────────────


def test_the_same_direction_scores_one():
    assert cosine_similarity([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)


def test_vectors_at_right_angles_score_zero():
    assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)


def test_opposite_vectors_score_minus_one():
    assert cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0)


def test_length_does_not_matter_only_direction():
    assert cosine_similarity([1, 1], [10, 10]) == pytest.approx(1.0)


def test_a_known_angle():
    assert cosine_similarity([1, 0], [1, 1]) == pytest.approx(0.7071, abs=1e-4)


def test_a_zero_vector_scores_zero_instead_of_failing():
    assert cosine_similarity([0, 0], [1, 1]) == 0.0
    assert cosine_similarity([1, 1], [0, 0]) == 0.0


def test_vectors_of_different_sizes_are_an_error():
    with pytest.raises(ValueError, match="different sizes"):
        cosine_similarity([1, 2], [1, 2, 3])


def test_rounding_never_pushes_the_score_past_one():
    # Plain division of these two gives 1.0000000000000002.
    a = [0.0864675897282483, 0.6637578048439807, 0.10793126209409987]
    b = [0.6052731280977381, 4.6463046339078655, 0.7555188346586991]
    assert cosine_similarity(a, b) <= 1.0


# ── mmr_select: order and size ───────────────────────────────


def test_without_vectors_the_best_matches_come_first():
    assert mmr_select([0.2, 0.9, 0.5], k=3) == [1, 2, 0]


def test_k_limits_the_number_chosen():
    assert mmr_select([0.9, 0.8, 0.7, 0.6], k=2) == [0, 1]


def test_asking_for_more_than_exist_returns_them_all():
    assert sorted(mmr_select([0.9, 0.8], k=10)) == [0, 1]


def test_zero_or_negative_k_returns_nothing():
    assert mmr_select([0.9, 0.8], k=0) == []
    assert mmr_select([0.9, 0.8], k=-3) == []


def test_no_candidates_returns_nothing():
    assert mmr_select([], k=5) == []
    assert mmr_select([], [], k=5) == []


def test_equal_scores_keep_the_earlier_candidate_first():
    assert mmr_select([0.5, 0.5, 0.5], k=3) == [0, 1, 2]


def test_the_inputs_are_not_changed():
    relevance = [0.9, 0.8]
    vectors = [[1.0, 0.0], [0.0, 1.0]]
    mmr_select(relevance, vectors, k=2)
    assert relevance == [0.9, 0.8]
    assert vectors == [[1.0, 0.0], [0.0, 1.0]]


# ── mmr_select: variety ──────────────────────────────────────

# Candidates 0 and 1 are near-copies. Candidate 2 is different and a weaker match.
RELEVANCE = [0.90, 0.89, 0.60]
VECTORS = [[1.0, 0.0], [1.0, 0.01], [0.0, 1.0]]


def test_the_first_pick_is_always_the_best_match():
    assert mmr_select(RELEVANCE, VECTORS, k=3, lambda_mult=0.0)[0] == 0


def test_a_near_copy_is_passed_over_for_something_different():
    assert mmr_select(RELEVANCE, VECTORS, k=2) == [0, 2]


def test_with_lambda_one_variety_is_ignored():
    assert mmr_select(RELEVANCE, VECTORS, k=2, lambda_mult=1.0) == [0, 1]


def test_with_lambda_zero_only_variety_counts():
    assert mmr_select(RELEVANCE, VECTORS, k=2, lambda_mult=0.0) == [0, 2]


def test_the_near_copy_still_comes_last_when_k_is_big_enough():
    assert mmr_select(RELEVANCE, VECTORS, k=3) == [0, 2, 1]


def test_variety_is_judged_against_everything_already_chosen():
    # 3 is a copy of 0, 4 is a copy of 2, and 1 is different from all of them.
    relevance = [0.9, 0.5, 0.8, 0.79, 0.78]
    vectors = [
        [1, 0, 0],
        [0, 0, 1],
        [0, 1, 0],
        [0.99, 0.1, 0],
        [0.1, 0.99, 0],
    ]
    assert mmr_select(relevance, vectors, k=3, lambda_mult=0.5) == [0, 2, 1]


# ── mmr_select: limits per group ─────────────────────────────


def test_a_group_cannot_supply_more_than_the_limit():
    picked = mmr_select(
        [0.9, 0.8, 0.7, 0.6],
        k=4,
        groups=["a", "a", "a", "b"],
        max_per_group=2,
    )
    assert picked == [0, 1, 3]


def test_the_limit_works_together_with_variety():
    picked = mmr_select(
        RELEVANCE,
        VECTORS,
        k=3,
        groups=["a", "a", "a"],
        max_per_group=1,
    )
    assert picked == [0]


def test_the_search_stops_when_every_group_is_full():
    picked = mmr_select([0.9, 0.8], k=5, groups=["a", "a"], max_per_group=1)
    assert picked == [0]


def test_groups_without_a_limit_change_nothing():
    assert mmr_select([0.9, 0.8, 0.7], k=3, groups=["a", "a", "a"]) == [0, 1, 2]


# ── mmr_select: bad input ────────────────────────────────────


def test_vectors_must_match_the_candidates():
    with pytest.raises(ValueError, match="vectors"):
        mmr_select([0.9, 0.8], [[1.0]], k=2)


def test_groups_must_match_the_candidates():
    with pytest.raises(ValueError, match="groups"):
        mmr_select([0.9, 0.8], k=2, groups=["a"])


@pytest.mark.parametrize("bad", [-0.1, 1.1])
def test_lambda_must_be_between_zero_and_one(bad):
    with pytest.raises(ValueError, match="lambda_mult"):
        mmr_select([0.9], k=1, lambda_mult=bad)


def test_the_group_limit_must_be_at_least_one():
    with pytest.raises(ValueError, match="at least 1"):
        mmr_select([0.9], k=1, groups=["a"], max_per_group=0)


def test_a_group_limit_needs_groups():
    with pytest.raises(ValueError, match="needs groups"):
        mmr_select([0.9], k=1, max_per_group=2)
