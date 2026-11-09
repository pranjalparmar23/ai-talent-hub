---
name: interview-rubric-authoring
description: Write or revise a scoring rubric for mock interviews in AI Talent Hub — dimensions, anchored 0–10 levels, fairness rules and the JSON output contract — for technical and non-technical roles. Use when adding an interview type, changing evaluation dimensions, or editing evaluation_prompts.py.
---

# Authoring an interview rubric

A rubric tells the evaluator what "good" looks like for one interview type, so scores are consistent, explainable and fair. Today's evaluator scores four dimensions from 0 to 10 (`communication`, `technical_knowledge`, `problem_solving`, `confidence`). Phase 9 extends interviews to every kind of role, so each interview type needs its own rubric.

Principles: **evidence over impression, anchored levels, content over delivery, fair across roles and speakers.**

## Step 1 — Define the interview type

Write down, before any scoring text:

- Who is being interviewed and for what kind of role (technical, behavioural, sales, design, operations, finance, healthcare support, and so on).
- What a competent hire does well in that role. Use your understanding of the role, and write the rubric in **your own words**. Do not copy interview guides or question banks from other sites.
- What the mock interview can realistically observe in a text or voice conversation. Do not score what a conversation cannot show.

## Step 2 — Choose dimensions

- Use **3 to 5** dimensions. More dimensions blur together and the model scores them inconsistently.
- Each dimension gets a one-line **definition**, and a one-line **not this** that keeps it apart from its neighbours (for example, `communication` is not the same as technical correctness).
- Prefer keeping the existing four where they fit; rename or replace them only when they do not describe the role (for example a sales rubric may use `discovery_questions` and `objection_handling`).
- Keys are `snake_case` and stable. Changing a key later breaks stored feedback and the UI.

## Step 3 — Anchor every level

For each dimension, describe what each band **looks like in a transcript**. Use observable behaviour, not adjectives.

| Band | Meaning |
|---|---|
| 0–2 | Absent, off-topic or incorrect |
| 3–4 | Present but vague, partial or shaky |
| 5–6 | Adequate: correct and relevant, little depth |
| 7–8 | Strong: specific, well reasoned, handles follow-ups |
| 9–10 | Outstanding: reserve for genuinely exceptional answers |

Example for `problem_solving` (technical role):
- 3–4: Jumps to an answer without stating assumptions; cannot explain why.
- 7–8: Clarifies the problem, compares at least two approaches, names trade-offs.

Rules that keep scoring honest:
- A short, vague or off-topic answer scores low. Do not inflate to be kind.
- Score the **candidate**, not the interviewer.
- Feedback cites what the candidate actually said.
- If a dimension was never observable in the transcript, say so instead of inventing a score (the code then fills it with the mean of the others, as it does today).

## Step 4 — Fairness rules (put them in the prompt)

- Judge **content, not delivery**: do not penalise accent, grammar, filler words, pauses or speech-recognition errors. Penalise unclear *reasoning*, not unclear *transcription*.
- Do not infer or use name, gender, age, nationality, school prestige or region.
- Do not reward length or confident tone by itself.
- Technical depth is judged against the role's needs, not against a software-engineering yardstick. Non-technical roles must not be scored as weak engineers.
- Text mode must always be a full alternative to voice.

## Step 5 — Output contract

- The prompt asks for **JSON only**: no markdown fences, no commentary, exactly the agreed keys.
- Literal braces in the prompt are doubled (`{{ }}`), because it goes through `ChatPromptTemplate`.
- Lists are capped (4 items each): `strengths`, `improvements`. `summary` is two sentences.
- **Never trust the model for arithmetic.** The overall score is computed in code from the dimension scores. Scores are clamped to 0–10 and coerced from strings.
- Parse with `parse_llm_json`. On any failure the agent returns the zeroed fallback with `_parse_error: True`.

## Step 6 — Author the rubric as a block

Write each rubric as one self-contained, versioned block, so it can move into whatever loader Phase 9 chooses:

```
id: <interview_type>            # e.g. behavioural, technical-backend, sales
version: 1
audience: <who and what role family>
dimensions:
  <key>:
    definition: <one line>
    not_this: <one line>
    anchors:
      "0-2": <observable behaviour>
      "3-4": ...
      "5-6": ...
      "7-8": ...
      "9-10": ...
fairness_notes: <anything specific to this type>
```

Increase `version` whenever wording or dimensions change, and record the id and version wherever feedback is stored, so scores from different versions are never compared as if equal.

## Step 7 — Wire it in (checklist when dimensions change)

- `backend/app/llm/prompts/evaluation_prompts.py`: the prompt text and JSON shape.
- `backend/app/agents/shared/evaluation_agent.py`: the `DIMENSIONS` tuple and `_normalize` (the `InterviewFeedbackResponse.scores` field is a plain dict, so the schema needs no change).
- `frontend/src/candidate/InterviewFeedback.tsx`: it names the dimensions.
- Tests: `tests/unit/agents/test_evaluation_agent.py` and `tests/unit/services/test_interview_service.py` reference the current keys.
- The golden set and evals (see the `eval-golden-set` skill).

## Step 8 — Validate before shipping

1. **Hand-score** at least 10 transcripts per rubric, covering weak, middling and strong answers and several role types.
2. Run the evaluator on the same transcripts and compare. Aim for most scores within ±1 of the human score; investigate every larger gap, then fix the **anchors**, not the data.
3. **Fairness probes:** take one strong answer and rewrite the surface (different name, a non-native phrasing, added filler words). The scores should not change materially.
4. Keep these cases as part of the golden set so later prompt or model changes are caught.

## Common mistakes

- Vague anchors ("good communication"). The model cannot tell a 6 from a 7.
- Dimensions that overlap, so one weakness is punished twice.
- Letting the model compute the overall score.
- One technical rubric reused for every role.
- Scoring delivery when the role does not need it.
- Changing keys or wording without bumping `version`.