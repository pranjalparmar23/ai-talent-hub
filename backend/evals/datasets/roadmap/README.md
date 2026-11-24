# Learning plan golden set

**This set tells us whether the learning plan got worse:** does it still link only to pages we retrieved, show its sources (or the "not based on reviewed sources" notice), rank the most trusted page first, and ignore instructions hidden in its input?

Part of Phase 7, task 13. Related: `docs/architecture/knowledge-layer.md`, `backend/app/agents/candidate/roadmap_agent.py` (the agent), `backend/evals/cases.py` (the case format), `backend/evals/metrics.py` (the checks).

## Files

| File | What it is |
|---|---|
| `links.v1.jsonl` | The cases, one JSON object per line. Never edited once published. |
| `links.v1.baseline.json` | The scores of the first reviewed run. Not added yet: the labels are still unchecked drafts (see below). |
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

Trying other ranking settings, with no model calls (fast, free and the same answer every time):

```
python -m evals.run_roadmap --ranking-only --split dev --verbose
python -m evals.run_roadmap --ranking-only --split dev --sweep trust_weight=0.3,0.4,0.5
python -m evals.run_roadmap --ranking-only --split dev --sweep trust.freecodecamp=0.8,0.7,0.6   # 0.7 became the default in task 13
python -m evals.run_roadmap --ranking-only --split dev --source-trust freecodecamp=0.7
```

`--ranking-only` checks only the cases that name a best page. Its overrides change that run only, and nothing is saved to the app. Tune on `dev`, confirm once on `test`, then change the value in `app/agents/candidate/roadmap_agent.py`.

Needs the learning collection filled first, and `GROQ_API_KEY` in `backend/.env`. A normal run makes one model call per case (none for the empty-plan case), so the whole set is about 26 calls and takes 6 to 12 minutes. The runner prints one line per case as it goes. The plan comes from a model with a little randomness (temperature 0.3). One failure on a single run can be chance, so for a result that matters use `--repeat` and look at the pass rate. The ranking check does not use the model, so it gives the same answer every time.

## Baseline and tolerance

| | Value |
|---|---|
| First run | `roadmap_v1_20261124T112658Z.json` in `data/eval/results/` (not copied to `links.v1.baseline.json`, because the labels are unchecked) |
| Date | 2026-11-24 |
| Settings | weights 0.6 / 0.3 / 0.1, freeCodeCamp trust 0.7, retrieval cutoff 0.40, 1 run per case |
| Knowledge base at the time | `interview_knowledge` 829 chunks, `learning_knowledge` 127 chunks |
| Model | `openai/gpt-oss-120b` |
| Pass rate (all) | 24 of 26 (0.92). Dev 18 of 19, test 6 of 7 |
| Agreed tolerance | not agreed yet (a drop of more than 0.05 was suggested) |

The run before the two changes (freeCodeCamp trust 0.8, cutoff 0.30) is `roadmap_v1_20261124T110737Z.json`: 22 of 26 (0.85), with the Docker pair (`rdm-003`, `rdm-015`), `rdm-008` and `rdm-017` failing. Compare with `python -m evals.run_roadmap --compare roadmap_v1_20261124T112658Z.json`. A single run per case can differ by chance, so use `--repeat` when a result matters.

The steps are the same as for the retrieval set (`../knowledge/README.md`): run it, read every case that disagrees, decide whether the label or the system is wrong, check the drafts, freeze `v1`, save the baseline, then agree a tolerance from what the system really does.

## Known weaknesses

- `rdm-008` (sales) fails: the plan linked `pytorch.org/tutorials` and `roadmap.sh/devrel` and had no notice. The agent searches "learning resources for ...", and those words are close to any tutorial page, so weak matches pass the cutoff. This is a real weakness of the agent's query, and the label is right. The likely fix is a shorter query, to be done as its own change with a `dev` run and one `test` confirmation. `rdm-009` (customer support) passes. Phase 9 adds non-technical sources.
- `rdm-017` (TypeScript, `test` split) fails: roadmap.sh ranks above typescriptlang.org, and both are linked. It was not tuned for. The label may be too strict. A person decides it when the labels are checked.
- The ranking cases (tag `ranking`) depend on the starting weights (0.6 match, 0.3 trust, 0.1 freshness). Two kinds are mixed on purpose. In most, the official page of a tool should come first (`rdm-002` to `rdm-005`, `rdm-015` to `rdm-021`, `rdm-026`). In the counter-cases (`rdm-022` to `rdm-025`) a community roadmap is the better match, and trust must not push a weaker official page above it. A weight that fixes one kind must not break the other. A failure may mean the weights need tuning, or that the label is wrong. Check which, case by case.
- The first run found one scenario where the official page did not rank first: Docker (`rdm-003`, `rdm-015`), where a freeCodeCamp topic page ranked above the official Get started page. Fixed on 2026-11-24 by lowering freeCodeCamp's trust from 0.8 to 0.7 (both cases now pass, and so do the counter-cases `rdm-023` and `rdm-024`).
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
| v1 | 2026-11-23 | Before the baseline was saved and v1 was frozen: added `rdm-016` to `rdm-026` (11 single-skill ranking cases, 8 dev and 3 test), because the first runs had only three distinct ranking scenarios. Now 26 cases (19 dev, 7 test), all `model-draft`. |
| v1 | 2026-11-24 | First run recorded (see above). No case or label changed. In the app, freeCodeCamp's trust changed from 0.8 to 0.7 and the retrieval cutoff from 0.30 to 0.40, after `dev` runs and one `test` confirmation. |