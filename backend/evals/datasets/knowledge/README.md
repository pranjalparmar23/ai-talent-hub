# Retrieval golden set

**This set tells us whether search got worse:** does it still find the right pages in `interview_knowledge` and `learning_knowledge`, and does it still return nothing when nothing fits?

Part of Phase 7, task 13. Related: `docs/architecture/knowledge-layer.md`, `backend/evals/cases.py` (the case format), `backend/evals/metrics.py` (the scoring).

## Files

| File | What it is |
|---|---|
| `retrieval.v1.jsonl` | The cases, one JSON object per line. Never edited once published. |
| `retrieval.v1.baseline.json` | The scores of the first reviewed run. Added after the baseline run (see below). |
| `README.md` | This file. |

## A case

```json
{"id": "ret-001", "area": "retrieval", "tags": ["technical", "clear", "interview", "fairness"],
 "split": "dev",
 "input": {"query": "how to solve dynamic programming problems in a coding interview",
           "collection": "interview_knowledge"},
 "expected": {"relevant_keywords": ["dynamic programming"]},
 "notes": "why this case exists", "source": "model-draft", "added": "2026-11-23",
 "pair_with": "ret-010"}
```

- `input` is what is sent to `RAGRetriever.search()`: the query, the collection, and optional `role_family`, `level`, `domain` and `top_k` (default 5).
- `expected` says what a relevant result looks like. It never names a chunk id, because chunk ids change when the knowledge base is rebuilt.
  - A returned chunk is **relevant** if its title or text contains one of `relevant_keywords` (any letter case), or its `source_id` is in `relevant_source_ids`, or its `domain` is in `relevant_domains`.
  - `expect_empty: true` means nothing should come back (an off-topic or empty query).
- `pair_with` links a fairness pair: two cases that differ only in wording or letter case. They must be in the same split.
- Cases use invented or self-written queries only. No real people, no text copied from a site.

## What is measured

| Metric | Meaning |
|---|---|
| precision | Relevant chunks returned divided by chunks returned. Measured over what came back, not over 5, because the 0.30 cutoff is meant to return fewer chunks when little matches. Returning nothing for a case that expects results scores 0. |
| hit rate | The share of cases where at least one returned chunk is relevant. |
| MRR | The mean of 1 / (rank of the first relevant chunk), 0 when there is none. |
| empty correct | For `expect_empty` cases: how many returned nothing. Not part of precision or MRR. |

Every report is given for all cases, each split and each tag, so a weak slice (non-technical roles, say) cannot hide in the overall number.

## Splits

Each case is `dev` or `test` (about a quarter are `test`). **Tune on `dev`.** Look at `test` only to confirm a change, so it stays a fair check. The split is written in the file and never changes.

## Checking the cases

From `backend/`:

```
python -m evals.cases --area retrieval
python -m evals.cases --area retrieval --require-reviewed
```

The second command fails while any case is still a `model-draft`. A case written by Claude does not count until a person has checked its expected values. When you have, change its `source` to `reviewed-draft`. This is allowed only **before** the version is frozen.

## Running it

This runs against the real ChromaDB and the real embedding model (nothing is faked). It is not part of `pytest tests/unit`.

From `backend/`:

```
python -m evals.run_retrieval                     # all cases
python -m evals.run_retrieval --split dev         # the cases to tune on
python -m evals.run_retrieval --split test        # only to confirm a change
python -m evals.run_retrieval --tag non-technical # one slice
python -m evals.run_retrieval --save              # also write the scores to a file
python -m evals.run_retrieval --compare retrieval.v1.baseline.json
```

Needs the knowledge collections to be filled first (`python -m app.ingest.run --source ...`).

## Baseline and tolerance

| | Value |
|---|---|
| Baseline run | not run yet |
| Date | |
| Knowledge base at the time | |
| precision / hit rate / MRR (all) | |
| Agreed tolerance | not agreed yet |

Steps (from the golden-set rules):

1. Run the whole set on the current system. That is the baseline.
2. Read every case that disagrees with its expectation. For each one decide: is the **label wrong** or is the **system wrong**? Fix the label (and write it in the changelog), or leave it and record it under known weaknesses.
3. Run `--require-reviewed`, then freeze `v1` and save the baseline file next to it.
4. Agree a tolerance with the team, for example "no metric drops more than N points against the baseline". The number comes from what the system actually does. It is not set before the first run.

## Known weaknesses

- `ret-032` and `ret-033` (sales, customer support): the learning links have no content for these yet, so they expect nothing. Weak matches on other business pages may get through the 0.30 cutoff. When Phase 9 adds non-technical sources, revise these in a new version.
- No `regression` cases yet. Every bug we really hit becomes a case, with the tag `regression`.

## Rules for changing this set

- Never edit a published version. A change is a new file `retrieval.v2.jsonl` with a line in the changelog below.
- Never change an expected value just to turn a run green. If a label was wrong, write why in the changelog.
- Ids are never reused.
- Cases that no longer test anything real (a source was retired, a rule changed) are removed or revised in a new version, and the changelog says so.
- Add a case for every bug and every surprising failure.
- Re-run after any change to retrieval settings (cutoff, MMR, results per page), the embedding model, the chunking, or the sources, and compare with the baseline.

## Where it goes next

- Phase 10 decides the final place for evaluation data and uploads these cases to a LangSmith dataset. The mapping is `input` to inputs, `expected` to reference outputs, `tags` and `source` to metadata. These files stay the source of truth.
- The Phase 2 set (`data/eval/retrieval_eval.json`, run by `backend/scripts/eval_retrieval.py`) targets the old `learning_resources` collection. It stays where it is until task 15 removes the old retrieval code.
- Task 15 deletes the old collections only after this set has been run and the baseline agreed.

## Changelog

| Version | Date | Change |
|---|---|---|
| v1 | 2026-11-23 | First set: 37 cases (28 dev, 9 test). All `model-draft`; to be checked by a person before the baseline is frozen. |