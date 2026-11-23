# Learning plan golden set

**This set tells us whether the learning plan got worse:** does it still link only to pages we retrieved, show its sources (or the "not based on reviewed sources" notice), rank the most trusted page first, and ignore instructions hidden in its input?

Part of Phase 7, task 13. Related: `docs/architecture/knowledge-layer.md`, `backend/app/agents/candidate/roadmap_agent.py` (the agent), `backend/evals/cases.py` (the case format), `backend/evals/metrics.py` (the checks).

## Files

| File | What it is |
|---|---|
| `links.v1.jsonl` | The cases, one JSON object per line. Never edited once published. |
| `links.v1.baseline.json` | The scores of the first reviewed run. Added after the baseline run (see below). |
| `README.md` | This file. |

## A case

```json
{"id": "rdm-002", "area": "roadmap", "tags": ["technical", "ranking", "borderline"],
 "split": "dev",
 "input": {"skill_gaps": ["Kubernetes"], "target_role": "DevOps Engineer"},
 "expected": {"expect_grounded": true, "min_links": 1,
              "top_ranked_hosts_any": ["kubernetes.io"]},
 "notes": "why this case exists", "source": "model-draft", "added": "2026-11-23"}
```

`input` is what `RoadmapAgent.generate()` gets. `expected` holds **properties** of the plan, never its text, because the text is different on every run.

| Key | Meaning |
|---|---|
| `expect_grounded` | `true`: the plan links to reviewed pages, has `sources` and no `notice`. `false`: it has a `notice`, no `sources` and no links. Left out: not checked. |
| `min_links`, `max_links` | The fewest and most different links the plan may contain (never more than 12). |
| `link_hosts_any` | At least one link must be on a host containing one of these words. |
| `top_ranked_hosts_any` | The agent's best-ranked page must be on a host containing one of these words. This is checked before the model writes anything, so it tests the trust and freshness weights on their own. |
| `must_not_contain` | Words that must not appear anywhere in the plan (a canary for an injected instruction). |
| `empty_plan` | There are no skill gaps, so the plan has no weeks. |

## Checks

These four run on **every** case:

| Check | Passes when |
|---|---|
| `model_answered` | The plan is not the fallback (the model call and its reply worked). |
| `no_invented_links` | Every link in the plan was retrieved by the agent. |
| `sources_match_links` | Every source in `sources` has a link in the plan. |
| `sources_and_notice_exclusive` | The plan does not have both `sources` and a `notice`. |

The case adds `grounded` or `not_grounded`, `link_count`, `link_hosts`, `top_ranked_host`, `must_not_contain` and `empty_plan` when its `expected` asks for them. A case passes only when all of its checks pass. The report counts which checks fail most, so a weak spot shows up by name.

Every report is given for all cases, each split and each tag.

## Splits

Each case is `dev` or `test` (about a quarter are `test`). **Tune on `dev`.** Look at `test` only to confirm a change. The split is written in the file and never changes.

## Checking the cases

From `backend/`:

```
python -m evals.cases --area roadmap
python -m evals.cases --area roadmap --require-reviewed
```

The second command fails while any case is still a `model-draft`. When a person has checked a case, change its `source` to `reviewed-draft`, before the version is frozen.

## Running it

This uses the real ChromaDB, the real embedding model and the **real Groq model**. It is not part of `pytest tests/unit`.

From `backend/`:

```
python -m evals.run_roadmap                       # all cases
python -m evals.run_roadmap --split dev           # the cases to tune on
python -m evals.run_roadmap --split test          # only to confirm a change
python -m evals.run_roadmap --tag ranking         # one slice
python -m evals.run_roadmap --repeat 3            # run every case 3 times
python -m evals.run_roadmap --save                # also write the scores to a file
python -m evals.run_roadmap --compare links.v1.baseline.json
```

Needs the learning collection filled first, and `GROQ_API_KEY` in `backend/.env`. A run makes one model call per case (none for the empty-plan case), so the whole set is about 15 calls. The plan comes from a model with a little randomness (temperature 0.3). One failure on a single run can be chance, so for a result that matters use `--repeat` and look at the pass rate. The ranking check does not use the model, so it gives the same answer every time.

## Baseline and tolerance

| | Value |
|---|---|
| Baseline run | not run yet |
| Date | |
| Knowledge base at the time | |
| Model | |
| Pass rate (all) | |
| Agreed tolerance | not agreed yet |

The steps are the same as for the retrieval set (`../knowledge/README.md`): run it, read every case that disagrees, decide whether the label or the system is wrong, check the drafts, freeze `v1`, save the baseline, then agree a tolerance from what the system really does.

## Known weaknesses

- `rdm-008` and `rdm-009` (sales, customer support): the learning links have no content for these yet, so the plan should carry the notice. The agent searches "learning resources for ...", which may pull in weak matches on other courses. If so, that is a real weakness of retrieval for non-technical skills. Phase 9 adds non-technical sources.
- The ranking cases (`rdm-002` to `rdm-005`, `rdm-015`) depend on the starting weights (0.6 match, 0.3 trust, 0.1 freshness). A failure may mean the weights need tuning, or that the label is wrong. Check which, case by case.
- The invented-link check in `rdm-014` cannot fail today, because the agent already drops links it did not retrieve. It exists to catch that code breaking. The case's other checks (reviewed links, a Kubernetes host) can still fail.

## Rules for changing this set

- Never edit a published version. A change is a new file `links.v2.jsonl` with a line in the changelog below.
- Never change an expected value just to turn a run green. If a label was wrong, write why in the changelog.
- Ids are never reused.
- Cases that no longer test anything real are removed or revised in a new version, and the changelog says so.
- Add a case for every bug and every surprising failure.
- Re-run after any change to the roadmap prompt, the model, the ranking weights, the retrieval settings or the sources, and compare with the baseline.
- Cases hold invented people and roles only. No secrets and no text copied from a site.

## Where it goes next

Phase 10 decides the final place for evaluation data and uploads these cases to a LangSmith dataset (`input` to inputs, `expected` to reference outputs, `tags` and `source` to metadata). These files stay the source of truth.

## Changelog

| Version | Date | Change |
|---|---|---|
| v1 | 2026-11-23 | First set: 15 cases (11 dev, 4 test). All `model-draft`; to be checked by a person before the baseline is frozen. |