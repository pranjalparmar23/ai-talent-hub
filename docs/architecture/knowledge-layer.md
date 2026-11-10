# Knowledge Layer Design

How AI Talent Hub stores and finds reference knowledge (interview material, learning resources) after the Phase 7 rebuild. This covers the schema, the collection layout and the embedding model. The pipeline that fills it is described in later tasks.

**Tracker:** Phase 7, tasks 2, 3 and 4. **Related:** `docs/architecture/mcp.md`, `docs/mcp/vetting-log.md`, `.claude/skills/content-source-vetting/SKILL.md`.
**Status:** design agreed 2026-11-10. Nothing in the app uses it yet; tasks 5 to 12 build on it.

---

## Decisions at a glance

| # | Decision | Choice |
|---|---|---|
| 1 | What one piece of knowledge looks like | A `KnowledgeDoc` record. Every chunk carries its source, licence and dates as flat metadata |
| 2 | Collections | Two new collections, `interview_knowledge` and `learning_knowledge`, filtered by metadata. `skills_taxonomy` is reserved for Phase 9 |
| 3 | Embedding model | **Keep `all-MiniLM-L6-v2` (English only).** Decided 2026-11-10 |
| 4 | Old data | Not migrated. It stays in the local snapshot and is deleted in task 15 after the new evaluation passes |

---

## Where we start

Facts from the repo and the snapshot taken on 2026-11-10:

- Five fixed collections hold 119 chunks in total: `behavioral_questions` 14, `company_interviews` 23, `dsa_notes` 18, `interview_experiences` 36, `learning_resources` 28. Chunks average about 350 characters.
- Metadata is free-form and differs per collection (`topic`, `category`, `company`, `difficulty`, `source_file` and so on). **None of it records where the text came from or what licence it has.** `source_file` names a file that is no longer in the repo.
- The interview agent searches `company_interviews` and `interview_experiences`. The roadmap agent searches `learning_resources`. Both use a similarity cutoff of 0.30 and show no sources.
- Chunks are 500 characters with 50 overlap (`rag/chunking.py`). Distance is cosine.
- ChromaDB metadata must be flat: values can only be text, whole numbers, decimals or true/false. Lists and nested objects are not allowed, and a missing value must be left out, not set to `None`.

---

## Decision 1: `KnowledgeDoc` and chunk metadata (task 2)

A **`KnowledgeDoc`** is one source page or file after fetching and cleaning. It is split into chunks, and every chunk carries the document's fields so that search results can show a source without a second lookup.

### Document fields

| Field | Type | Meaning |
|---|---|---|
| `source_id` | text | Which approved source this came from. Must match an entry in `docs/mcp/vetting-log.md` (for example `tech-interview-handbook`) |
| `url` | text | Canonical URL of the page or file |
| `title` | text | Page title, shown as the citation |
| `domain` | text | Subject area, for example `software-engineering`, `data`, `product`, `design`, `marketing`, `finance`, `hr`, `general` |
| `role_family` | text | `technical`, `non_technical` or `all` (same vocabulary as the tracing helper) |
| `level` | text | `entry`, `mid`, `senior` or `all` |
| `content_type` | text | See the table in Decision 2 |
| `ingest_mode` | text | `full_text` or `link_only` (from the vetting verdict) |
| `licence` | text | SPDX identifier such as `MIT` or `CC-BY-4.0`, or `proprietary` for link-only sources |
| `attribution` | text | The exact credit line to show, for example "Tech Interview Handbook, MIT licence" |
| `language` | text | `en` only (see Decision 3) |
| `fetched_at` | ISO time | When we fetched it |
| `expires_at` | ISO time | When it must be refreshed or removed |
| `content_hash` | text | Fingerprint of the cleaned text, the embedding model and the chunk settings |
| `text` | text | The cleaned text. For `link_only` sources this is **our own** short description (title and topic tags), never the source's content |

### Chunk metadata (what is stored in ChromaDB)

Every document field except `text` and the ISO times, plus:

| Field | Type | Meaning |
|---|---|---|
| `doc_id` | text | First 16 hex characters of SHA-256 over `source_id`, `|` and `url` |
| `chunk_index` | whole number | Position in the document |
| `schema_version` | whole number | `2` for this layout |
| `embedding_model` | text | The model that produced the vector |
| `fetched_at_ts`, `expires_at_ts` | whole number | The same dates as Unix seconds, so ChromaDB can filter on them (it can compare numbers, not date text) |

### Rules

1. **Deterministic IDs.** Chunk id is `<doc_id>:<chunk_index as 4 digits>`. The same content always gets the same ids, so loading twice changes nothing.
2. **Idempotent updates.** Same `content_hash`: skip. Changed hash: delete all chunks with that `doc_id`, then add the new ones. Past `expires_at`: delete. Every run logs added, updated, skipped and expired counts.
3. **The hash includes the embedding model and chunk settings,** so changing either forces a re-embed.
4. **Flat values only,** and missing values are left out.
5. **Public content only.** Resumes, interview answers and anything from a user never go into these collections.
6. **No licence, no ingest.** A document without a recorded licence and attribution is refused (task 7).

---

## Decision 2: collections (task 3)

Five topic-based collections are replaced by two content-based ones. Topic, role and level become filters, so adding a new domain needs no new collection.

| Collection | Holds | `content_type` values |
|---|---|---|
| `interview_knowledge` | What the interview coach draws on | `interview_question`, `behavioral_question`, `experience`, `company_pattern`, `concept_note` |
| `learning_knowledge` | What the learning plan draws on | `course`, `article`, `video`, `roadmap`, `docs` |
| `skills_taxonomy` (reserved) | Occupations and skills. Created in Phase 9 | n/a |

Searches filter on `domain`, `role_family`, `level` and `content_type`, and always on `schema_version = 2`.

**Why new names instead of reusing `learning_resources`:** the old agents keep working until they are rewired in task 11, and task 15 can delete the old five collections cleanly without any risk to the new ones. This differs from the example names in the tracker for that reason only.

**Old data:** the 119 old chunks have no recorded source or licence, so under the rule above they cannot be loaded into the new collections. They remain in the local snapshot and are deleted from ChromaDB in task 15, **only after** the new evaluation set passes.

---

## Decision 3: embedding model (task 4)

**Keep `all-MiniLM-L6-v2`.** English only. Decided by the project owner on 2026-11-10.

Why this is fine for now:
- Everything we plan to ingest in Phase 7 is English.
- It runs locally with no API key, and chunks of 500 characters sit well inside its input limit.
- Changing the model is possible later; it only costs a rebuild.

What follows from it:
- Ingest refuses any document whose `language` is not `en`.
- Every chunk records its `embedding_model`.
- If the model ever changes, **all** collections must be rebuilt together. Vectors from different models cannot be compared. The pipeline detects this through the content hash.

**Revisit when:** Phase 9 decides to serve non-English users, or the Phase 7 evaluation shows weak retrieval that better embeddings could fix. Record the new decision here with its date.

---

## What the rest of Phase 7 builds on this

| Task | Uses |
|---|---|
| 5 | Pipeline writes `KnowledgeDoc` chunks with deterministic ids. Needs an upsert, because `VectorStore.add_documents` currently adds with random ids |
| 6, 7 | Source adapters fill the fields. The licence gate checks `licence`, `attribution` and `ingest_mode` |
| 8 | Refresh and expiry use `expires_at_ts` and `content_hash` |
| 9 | Retriever v2 filters on the fields above and returns `title`, `url`, `licence` and `attribution` as citations |
| 11, 12 | The agents return `sources[]` built from the chunk metadata |
| 13, 14 | The evaluation set and tests cover the filters, idempotency and the licence gate |

---

## Findings from reading the code

- `RetrieverAgent` (`agents/shared/retriever_agent.py`) is used nowhere and would fail if it were: it `await`s a function that is not async and passes `collection=` where the retriever expects `collection_name=`. Remove it, or fix it, during task 9.
- ChromaDB's `$in` and numeric comparisons are assumed to work as documented. Task 14 proves that with tests against the real client.

## Open items

- Default `expires_at` per source type. Starting point: 90 days for a static repository, shorter for anything that changes often. Tune in task 8.
- The exact licence allowlist (for example how to treat `CC-BY-SA`, which has share-alike terms) belongs to task 7 and needs a human decision.
- The `domain` list is a starting set. Phase 9 will extend it for non-technical roles.

## Changelog

| Date | Change |
|---|---|
| 2026-11-10 | First version: schema, two-collection layout, embedding model kept |