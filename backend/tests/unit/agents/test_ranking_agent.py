"""Unit tests for RankingAgent (pure Python, no LLM)."""

import copy

from app.agents.recruiter.ranking_agent import RankingAgent

agent = RankingAgent()


def _c(cid, score, **extra):
    return {"candidate_id": cid, "match_score": score, **extra}


class TestOrdering:
    def test_sorted_best_first(self):
        ranked = agent.rank([_c("a", 40), _c("b", 90), _c("c", 65)])
        assert [c["candidate_id"] for c in ranked] == ["b", "c", "a"]

    def test_ranks_assigned_from_one(self):
        ranked = agent.rank([_c("a", 40), _c("b", 90), _c("c", 65)])
        assert [c["rank"] for c in ranked] == [1, 2, 3]

    def test_equal_scores_share_a_rank_then_skip(self):
        ranked = agent.rank([_c("a", 80), _c("b", 80), _c("c", 70), _c("d", 90)])
        assert [c["rank"] for c in ranked] == [1, 2, 2, 4]

    def test_tie_broken_by_skill_then_semantic_then_id(self):
        ranked = agent.rank(
            [
                _c("d", 80, skill_match=50, semantic_match=50),
                _c("c", 80, skill_match=60, semantic_match=10),
                _c("b", 80, skill_match=50, semantic_match=70),
                _c("a", 80, skill_match=50, semantic_match=50),
            ]
        )
        assert [c["candidate_id"] for c in ranked] == ["c", "b", "a", "d"]

    def test_order_independent_of_input_order(self):
        candidates = [_c(f"id{i}", 70) for i in range(6)]
        forward = agent.rank(candidates)
        backward = agent.rank(list(reversed(candidates)))
        assert [c["candidate_id"] for c in forward] == [
            c["candidate_id"] for c in backward
        ]

    def test_name_never_used_as_tiebreaker(self):
        ranked = agent.rank([_c("b", 80, name="Aaron"), _c("a", 80, name="Zed")])
        assert [c["candidate_id"] for c in ranked] == ["a", "b"]


class TestUnscored:
    def test_unscored_listed_last_with_no_rank(self):
        ranked = agent.rank([_c("x", None), _c("a", 10), _c("y", "n/a"), _c("b", 0)])
        assert [c["candidate_id"] for c in ranked] == ["a", "b", "x", "y"]
        assert [c["rank"] for c in ranked] == [1, 2, None, None]

    def test_zero_is_a_real_score_not_unscored(self):
        ranked = agent.rank([_c("a", 0)])
        assert ranked[0]["rank"] == 1

    def test_missing_key_nan_and_bool_are_unscored(self):
        ranked = agent.rank(
            [{"candidate_id": "a"}, _c("b", float("nan")), _c("c", True), _c("d", 5)]
        )
        assert ranked[0]["candidate_id"] == "d"
        assert [c["rank"] for c in ranked[1:]] == [None, None, None]


class TestBehaviour:
    def test_input_not_mutated(self):
        original = [_c("a", 40), _c("b", 90)]
        snapshot = copy.deepcopy(original)
        agent.rank(original)
        assert original == snapshot

    def test_extra_fields_preserved(self):
        ranked = agent.rank([_c("a", 50, matching_skills=["Python"], name="A")])
        assert ranked[0]["matching_skills"] == ["Python"]
        assert ranked[0]["name"] == "A"

    def test_top_n(self):
        ranked = agent.rank([_c(str(i), i) for i in range(10)], top_n=3)
        assert [c["candidate_id"] for c in ranked] == ["9", "8", "7"]

    def test_top_n_zero_and_larger_than_list(self):
        assert agent.rank([_c("a", 1)], top_n=0) == []
        assert len(agent.rank([_c("a", 1)], top_n=50)) == 1

    def test_empty_none_and_junk_inputs(self):
        assert agent.rank([]) == []
        assert agent.rank(None) == []
        assert [c["candidate_id"] for c in agent.rank([None, 3, "x", _c("a", 1)])] == [
            "a"
        ]

    def test_integer_and_float_scores_compare(self):
        ranked = agent.rank([_c("a", 80), _c("b", 80.5)])
        assert ranked[0]["candidate_id"] == "b"
