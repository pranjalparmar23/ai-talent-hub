---
name: eval-golden-set
description: Build and maintain a golden evaluation set for AI Talent Hub — small, hand-checked, versioned cases with expected outcomes, used to catch regressions when prompts, models or retrieval change. Use when creating eval data for an agent or RAG, adding cases after a bug, setting baselines, or preparing datasets for LangSmith evals.
---

# Building a golden set

A golden set is a small collection of inputs with **hand-verified expected outcomes**. We run the system over it after every meaningful change (a prompt edit, a new model, a retrieval rebuild) and compare with the baseline. It answers one question: *did this change make things better or worse?*

Groq retires models every few months and the RAG layer is being rebuilt in Phase 7, so we will re-run these sets often.

## Golden sets vs unit tests

| | Unit tests | Golden-set evals |
|---|---|---|
| Model | Replaced by `FakeChain`, no network | The **real** model or retriever |
| Checks | Code logic, parsing, clamping, fallbacks | Output **quality** |
| Runs | Every CI run | On demand or nightly, so we stay inside LangSmith's trace cap |
| Deterministic | Yes | No, so judge by metrics and tolerances, not exact equality |

Both are needed. Do not put golden-set runs in `pytest tests/unit/`.

## Step 1 — Decide what decision it protects

Write one sentence: "This set tells us whether *X* got worse." Examples: retrieval still finds the right interview notes; the ATS score stays sensible; the evaluator still scores like a human. If you cannot write the sentence, the set has no purpose yet.

## Step 2 — Pick the case type and metric

| Area | A case is | Expected | Metric |
|---|---|---|---|
| Retrieval (RAG) | A query | What a relevant result must contain: expected keywords and metadata topics (the Phase 2 method), or stable document ids | precision@K, MRR |
| Extraction (resume parser, JD analyzer) | A synthetic resume or JD | The fields it must return | Field-level match / F1 |
| Scoring (ATS, interview evaluation) | A resume + JD, or a transcript | A human score or range | Within ±1 (0–10) or ±10 (0–100); rank agreement |
| Generation (questions, roadmap) | A role and context | **Properties**, not text: covers the topic, no repeated question, links present | Rule checks or an LLM judge in LangSmith |
| Safety | An adversarial input | The behaviour required: ignore the injected instruction, no PII leaked | Pass or fail per case |

For generation never store "the right answer" as exact text; store what a good answer must contain.

## Step 3 — Choose the cases

Aim for **20–50 cases to start**: small enough to review by hand, big enough to show a trend. Cover a grid, not just the easy middle:

- **Role families:** technical, and non-technical (sales, support, design, operations, finance and others).
- **Difficulty:** clear wins, clear failures, borderline.
- **Messy reality:** short answers, off-topic answers, long rambling ones, unusual formatting, empty input.
- **Bugs we have actually hit.** Every real bug becomes a case.
- **Fairness slices:** paired cases that differ only in surface (name, phrasing, filler words). The scores should match.
- **Safety cases:** injection text inside a resume, a JD or MCP output.

## Step 4 — Write the case

One JSON object per line (JSONL). Keep it flat and readable:

```json
{"id": "ats-007", "area": "ats", "tags": ["non-technical", "borderline"],
 "input": {"resume": {...}, "jd": {...}},
 "expected": {"score_min": 55, "score_max": 70},
 "notes": "why this case exists and what it protects",
 "source": "hand-written", "added": "2026-11-09"}
```

- `id` is stable and never reused. `tags` allow reporting by slice. `notes` records **why** the case exists.
- Expected values are written or verified by a **human**. If a second person can review, do that.
- Model-generated cases are allowed as drafts, but mark `source` accordingly and verify every expected value by hand before the case counts.

## Step 5 — Privacy and rights

- **Synthetic data only.** Invent people, companies and transcripts. No real resumes, names, emails, phone numbers or voice transcripts.
- Write queries and documents yourself. Do not paste copyrighted text from sites or books.
- No secrets in cases.

## Step 6 — Baseline and thresholds

1. Run the current system over the set and record the scores. That is the **baseline**.
2. Read every case that disagrees with the expectation. Decide each time: is the **label wrong** or is the **system wrong**? Fix the label, or record the case as a known weakness.
3. Freeze the set as v1. Then agree a **tolerance** with the team (for example, "no metric drops more than N points against baseline"). Do not invent thresholds up front; start from what the system actually does.

Hold out a **test split** (about 20–30% of cases) that is never used while tuning prompts. Check it only to confirm a change.

## Step 7 — Store and version it

The Phase 2 retrieval set (`data/eval/retrieval_eval.json`: 20 keyword-based pairs, run by `backend/scripts/eval_retrieval.py`) is the first golden set; keep it where it is until Phase 10 decides.

Proposed layout for new sets (confirm against Phase 10 before creating it, and follow Phase 10 if it has already decided otherwise):

```
backend/evals/datasets/<area>/<name>.v<N>.jsonl
backend/evals/datasets/<area>/README.md      # purpose, metric, baseline, changelog
```

- Never edit a published version in place. Add `v2` with a changelog line.
- Never change an expected value just to make a run pass. If a label was wrong, record why in the changelog.
- RAG cases that use document ids need **stable** ids; when a corpus is rebuilt (Phase 7), re-map them and re-baseline. Cases that use expected keywords and topics (the Phase 2 style) survive a rebuild better, because they do not depend on ids.

## Step 8 — Keep it alive

- Add a case for every bug and every surprising failure.
- Re-run after any prompt, model or retrieval change and compare with the baseline.
- Remove or revise cases that no longer test anything real (a source was retired, a rubric changed). Record it in the changelog.
- Report results **per tag** as well as overall. An overall number can hide a failure on non-technical roles.

## LangSmith (Phase 10)

A case maps to a LangSmith example: `input` → inputs, `expected` → reference outputs, `tags` and `source` → metadata. Phase 10 handles upload, evaluators and scheduling. The same JSONL stays the source of truth, so the data is not locked into a tool.

## Common mistakes

- Starting with 500 unreviewed cases instead of 30 verified ones.
- Only happy-path cases, so everything "passes".
- Exact-text expectations for generated content.
- Tuning on every case, including the held-out ones.
- Real user data in a dataset.
- Editing expectations to turn a red run green.
- One average number hiding a bad slice.
