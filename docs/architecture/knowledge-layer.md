# Knowledge Layer Design

How AI Talent Hub stores and finds reference knowledge (interview material, learning resources) after the Phase 7 rebuild. This covers the schema, the collection layout, the embedding model, and how the pipeline, the licence gate and the source adapters fill the collections.

**Tracker:** Phase 7, tasks 2 to 9. **Related:** `docs/architecture/mcp.md`, `docs/mcp/vetting-log.md`, `docs/attribution.md`, `.claude/skills/content-source-vetting/SKILL.md`.
**Status:** design agreed 2026-11-10. The schema, collections, ingestion pipeline, licence gate, two source adapters, and the ingest command with its expiry sweep are built and unit tested (updated 2026-11-19). Both sources were ingested for real on 2026-11-19 (see Decision 5). The unit tests still use fakes. The retriever can search them (task 9, Decision 6), but no agent uses it yet (task 11).

---

## Decisions at a glance

| # | Decision | Choice |
|---|---|---|
| 1 | What one piece of knowledge looks like | A `KnowledgeDoc` record. Every chunk carries its source, licence and dates as flat metadata |
| 2 | Collections | Two new collections, `interview_knowledge` and `learning_knowledge`, filtered by metadata. `skills_taxonomy` is reserved for Phase 9 |
| 3 | Embedding model | **Keep `all-MiniLM-L6-v2` (English only).** Decided 2026-11-10 |
| 4 | Old data | Not migrated. It stays in the local snapshot and is deleted in task 15 after the new evaluation passes |
| 5 | Licence policy | Allowlist of permissive licences for full text. Share-alike, unknown and restrictive licences are refused. Restrictive sources are link-only |
| 6 | Where content comes from | One adapter per source. Pilot: Tech Interview Handbook (full text) and roadmap.sh (link only) |
| 7 | How content is loaded and kept fresh | One command (`python -m app.ingest.run`). After each run, chunks of that source that are past `expires_at` are removed |
| 8 | How content is found | `RAGRetriever.search()`: filters in ChromaDB, skips expired chunks, drops weak matches, avoids near-duplicates, returns citations. Details in Decision 6 below |

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

A **`KnowledgeDoc`** is one source page or file after fetching and cleaning. It is split into chunks, and every chunk carries the document's fields so that search results can show a source without a second lookup. The code is `app/ingest/schema.py`.

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
| `doc_id` | text | First 16 hex characters of SHA-256 over `source_id`, `\|` and `url` |
| `chunk_index` | whole number | Position in the document |
| `schema_version` | whole number | `2` for this layout |
| `embedding_model` | text | The model that produced the vector |
| `fetched_at_ts`, `expires_at_ts` | whole number | The same dates as Unix seconds, so ChromaDB can filter on them (it can compare numbers, not date text) |

### Rules

1. **Deterministic IDs.** Chunk id is `<doc_id>:<chunk_index as 4 digits>`. The same content always gets the same ids, so loading twice changes nothing.
2. **Idempotent updates.**
   - Same `content_hash` and same chunk ids: nothing is re-embedded, but the dates and credit line are refreshed, so content that was just re-checked does not expire.
   - Changed hash: the new chunks are written first, then the old ids that are no longer part of the document are deleted. A failure half way leaves the old content, not nothing.
   - A document whose `content_type` moves it to the other collection is removed from the old collection after the new write succeeds.
   - Past `expires_at`: delete (task 8).
3. **The hash includes the embedding model and chunk settings,** so changing either forces a re-embed.
4. **Flat values only,** and missing values are left out.
5. **Public content only.** Resumes, interview answers and anything from a user never go into these collections.
6. **No licence, no ingest.** A document without a recorded licence and attribution is refused by the licence gate (Decision 4).

---

## Decision 2: collections (task 3)

Five topic-based collections are replaced by two content-based ones. Topic, role and level become filters, so adding a new domain needs no new collection. The code is `app/rag/collections.py`; the old five stay listed there as legacy until task 15.

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

## Decision 4: pipeline, licence gate and source adapters (tasks 5, 6, 7)

Three parts, each with one job:

```
source adapter  ->  licence gate  ->  pipeline  ->  ChromaDB
(fetch + clean)    (may we store it?)  (chunk, embed, write)
```

### The pipeline (task 5)

Code: `app/ingest/pipeline.py`. Chunks are 500 characters with 50 overlap. For each document it:

1. runs the licence gate (a refused document is reported, never stored);
2. fingerprints the text and how it is embedded (`content_hash`);
3. looks at what is already stored for the document;
4. writes new or changed chunks with fixed ids, or only refreshes the dates and credit line when nothing changed;
5. deletes chunks that are no longer part of the document.

Each document ends with one status:

| Status | Meaning |
|---|---|
| `added` | New document written |
| `updated` | Content changed and was rewritten |
| `unchanged` | Same content; only the dates and credit line were refreshed |
| `refused` | The licence gate said no. This is not a failure |
| `skipped` | No text to store |
| `failed` | An error. The run goes on with the next document |

A run is `ok` unless a document `failed`. The collections must exist first (`python scripts/init_chroma.py`). The pipeline uses four `VectorStore` methods added for it: `upsert_documents`, `get_by_where`, `update_metadatas` and `delete_ids`. The older methods are unchanged.

### The licence gate (task 7)

Code: `app/ingest/licence.py`. The rule is "no licence, no ingest".

| Licence | Full text | Link only |
|---|---|---|
| MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, 0BSD, Unlicense, CC0-1.0, CC-BY-3.0, CC-BY-4.0 | Allowed, with credit | Allowed |
| CC-BY-SA-3.0, CC-BY-SA-4.0 (share-alike) | **Refused** until a person decides | Allowed |
| Anything else (GPL, non-commercial, custom, `proprietary`) | Refused | Allowed |
| Missing, blank, or a word such as "unknown" | Refused | Refused |

Link-only documents store only our own short description and the link, so the source's licence does not limit us. A licence must still be recorded. Licence names are written in their standard form (`mit` becomes `MIT`). To allow a licence, add it to `ALLOWED_FULL_TEXT`. This is a code change on purpose, so it goes through review.

### Source adapters (task 6)

Code: `app/ingest/sources/`. An adapter knows one source: where its pages are, what to skip, how to clean them and which licence and credit line apply. It returns a `SourceBatch` with the documents and a list of what it skipped and why. It never writes to ChromaDB; the pipeline does.

| Source | Adapter | Mode | Licence | How it is read | Result |
|---|---|---|---|---|---|
| Tech Interview Handbook | `tech_interview_handbook.py` | full text | MIT, checked on every run | GitHub MCP server, through the gateway | 45 pages on the copy checked (2026-08-07), 4 files skipped |
| roadmap.sh | `link_only.py` with `data/roadmap_sh.yaml` | link only | `proprietary` (links only) | Our own YAML list. No network, no MCP | 41 links with descriptions written by us |

**Reading files.** Adapters read through a small `SourceReader` interface. In production that is `GatewayGitHubReader` (`readers.py`), which goes through the MCP gateway and refuses a failed call, old cached data, a result cut at `max_result_chars`, and an empty reply. In tests it is an in-memory reader, so the adapters run with no network.

**Handbook cleaning.** Before storing, the adapter removes page headers, imports, components, HTML and images; links keep their text but lose the address; paragraphs, list items and table rows with affiliate links or sales wording are removed. Code blocks and inline code are never changed. A page that has under 200 characters left is skipped. It skips `resume-old.md`, `best-coding-interview-courses.md`, `mock-interviews.md` and template files, and records the reason for each. Each page is classified into a `content_type`, `role_family` and `level` from its file name, so behavioural pages go to `interview_knowledge` and the study plan goes to `learning_knowledge`.

**Link-only lists.** Each entry needs a URL, title, domain, role family and a one-sentence summary. A bad or repeated entry is skipped with a reason and the rest still load. The text stored is the title and the summary.

**Credit.** Each source's credit line and licence notice are recorded in `docs/attribution.md`.

### Adding a source

1. Vet it and record the verdict in `docs/mcp/vetting-log.md`.
2. Read its licence file itself.
3. Full text: add an adapter in `app/ingest/sources/` that returns a `SourceBatch`. Link only: add a YAML list and use `LinkOnlyAdapter`.
4. Register it in `SOURCES` in `app/ingest/run.py`. List any MCP server it needs in `servers`.
5. Add it to `docs/attribution.md`.
6. Add tests. Run `python -m app.ingest.run --source <name> --dry-run`, then a real run, and check the stored credit line.

---

## Decision 5: the ingest command and expiry (task 8)

Code: `app/ingest/run.py` (the command) and `app/ingest/expiry.py` (finding and removing expired chunks).

```
python -m app.ingest.run --source tech-interview-handbook --enable-server github
python -m app.ingest.run --source roadmap-sh
python -m app.ingest.run --source all --dry-run
```

Run it from `backend/`. ChromaDB must be running (`CHROMA_HOST` and `CHROMA_PORT` in `backend/.env`).

### What one run does

For each chosen source, in order:

1. Ask the source adapter for its documents.
2. Give them to the pipeline. (A dry run only checks the licence gate.)
3. Remove that source's chunks that are past their expiry date.
4. Print a report: fetched, added, updated, unchanged, refused, skipped, failed and expired, with the reason for anything refused, skipped or failed.

A source that cannot be read is reported as `PROBLEM`. The other sources still run.

### Options

| Option | Effect |
|---|---|
| `--source NAME` or `--source all` | Required. `NAME` is `tech-interview-handbook` or `roadmap-sh` |
| `--dry-run` | Fetch and check licences, and list expired chunks. Writes and deletes nothing, and creates no collections |
| `--no-expire` | Do not remove expired chunks |
| `--enable-server NAME` | Switch an MCP server on for this run only |

Exit code: `0` when nothing failed, `1` when a document or a whole source failed (a refused document is not a failure), `2` for a bad argument or an unknown server name.

### Expiry

- A chunk is expired when its `expires_at_ts` is now or earlier.
- Running a source refreshes the dates of every page that is still in it. Unchanged pages are not embedded again. So what gets removed is content that left the source, or that was not refreshed within its window (90 days by default).
- Only the chunks of the source being run are removed, in both knowledge collections. Running `roadmap-sh` never removes handbook content. The old legacy collections are never touched.
- The sweep is skipped when the source could not be read, so a temporary outage does not delete anything. A document that failed keeps its old chunks until they expire.
- The retriever ignores expired chunks when searching (Decision 6), because they stay stored until the next run of their source.

### The `github` server stays off by default

`github` is `enabled: false` in `mcp_servers.yaml`, and a test requires every bundled server to be off. `--enable-server github` switches it on for one run, in memory. The file is not changed. Without the flag, the handbook source stops at once and names the flag to use.

### First real run of the handbook

1. Put a read-only, fine-grained token in `backend/.env` as `GITHUB_PERSONAL_ACCESS_TOKEN`. Never commit it.
2. `docker pull ghcr.io/github/github-mcp-server:v1.14.0`.
3. `python -m app.ingest.run --source tech-interview-handbook --enable-server github --dry-run`. This reads from GitHub and writes nothing. On the copy checked on 2026-08-07 it gave 45 documents and 4 left out.
4. Run it again without `--dry-run`, then a third time. The third run should report every document as `unchanged`.

Done on 2026-11-19. The repeat run reported 45 documents `unchanged`, 4 left out, 0 refused and 0 failed.

Stdio servers are refused in production (see `docs/architecture/mcp.md`, section 10), so run this from a development or CI machine.

---

## Decision 6: retrieval (task 9)

Code: `app/rag/retriever.py` (`RAGRetriever.search()`), `app/rag/ranking.py` (variety) and `VectorStore.query` in `app/rag/vector_store.py`.

```python
result = RAGRetriever().search(
    "tell me about a time you led a project",
    "interview_knowledge",
    role_family="technical",
    level="mid",
)
result.chunks      # best match first: .text, .similarity, .metadata, .citation
result.citations   # one {title, url, licence, attribution} per page
result.empty       # True when nothing usable was found
```

`search()` is the way to read the two knowledge collections. The old `retrieve()` stays until task 11 moves the agents over, and task 15 removes it.

### What one search does

1. Check the arguments. A typo is an error, not an empty result.
2. Build the filter (below) and ask ChromaDB for several times `top_k` candidates (4 times, and at least 20), because some will be dropped.
3. Drop candidates below the similarity cutoff.
4. Pick `top_k` of the rest, best first but varied (MMR), with at most 2 chunks from one page.
5. Return the chunks with their citations and counts, and log the counts and the time taken.

### The filter

Always applied:

- `schema_version` is the current one. Chunks written with an older schema are never returned.
- `expires_at_ts` is later than now. Expired chunks stay stored until the next ingest run of their source, so the retriever has to skip them itself.

Optional, each one narrows the search:

| Argument | Matches |
|---|---|
| `domain` | that domain, or `general` |
| `role_family` | that value, or `all` |
| `level` | that value, or `all` |
| `content_type` | one type or a list of types. Each must belong to the collection being searched |

Content marked `all` or `general` fits any request, so a general interview question is not hidden from a "technical, mid" search.

### Defaults

| Setting | Default | Meaning |
|---|---|---|
| `top_k` | 5 | Results returned |
| `min_similarity` | 0.30 | Below this a match is noise. It is the cutoff the agents use today |
| `mmr` | on | Pick varied results. `mmr=False` gives plain best-first |
| `mmr_lambda` | 0.7 | 1.0 means only relevance, 0.0 means only variety |
| `max_per_doc` | 2 | Most chunks one page may supply. `None` or `0` switches it off |
| candidates fetched | 4 x `top_k`, at least 20 | Extra candidates so the cutoff and variety have something to choose from |

These are starting values. Task 13 (the golden set) tunes them.

### The result

`SearchResult` has `chunks`, `citations` and `empty`, plus two counts that say why a result is empty:

- `candidates` is how many chunks passed the filters.
- `below_cutoff` is how many of those were dropped as weak.

Empty with `candidates` 0 means nothing matched the filters (for example a domain with no content). Empty with `below_cutoff` above 0 means chunks matched, but weakly. Task 10 (live lookup) and the empty-retrieval metric use this.

### Why these choices

- Similarity is `1 - distance`. That is correct because the collections use cosine distance.
- MMR is there because the handbook pages overlap. Without it the top 5 can be five near-copies of one idea.
- The per-page limit stops one long page from filling the whole result.
- The ranking maths is plain Python. It only ever handles about 20 vectors, so it needs no numpy.
- ChromaDB returns the stored vectors on request (`include_embeddings`), so nothing is embedded a second time.

### What it does not do

- It searches one collection per call. A caller that wants both makes two calls.
- It does not look anything up live when the result is empty. That is task 10.
- It is vector search only (no keyword search), and English only, because of the embedding model.

---

## What the rest of Phase 7 builds on this

| Task | Uses |
|---|---|
| 5 | **Built.** Pipeline writes `KnowledgeDoc` chunks with deterministic ids through `upsert_documents` |
| 6, 7 | **Built.** Source adapters fill the fields. The licence gate checks `licence`, `attribution` and `ingest_mode` |
| 8 | **Built.** `python -m app.ingest.run` runs the adapters and the pipeline. Refresh uses `content_hash`; expiry uses `expires_at_ts`. Both sources ran for real on 2026-11-19 |
| 9 | **Built.** Retriever v2 (`RAGRetriever.search()`) filters on the fields above, skips expired chunks, and returns `title`, `url`, `licence` and `attribution` as citations |
| 11, 12 | The agents return `sources[]` built from the chunk metadata |
| 13, 14 | The evaluation set and tests cover the filters, idempotency and the licence gate |

---

## Findings from reading the code

- `RetrieverAgent` (`agents/shared/retriever_agent.py`) was used nowhere and could not have worked: it `await`ed a function that is not async and passed `collection=` where the retriever expects `collection_name=`. Removed in task 9.
- The real ingest runs on 2026-11-19 worked with ChromaDB's `upsert`, `get` with `where`, and `update` calls, and ChromaDB accepted the expiry filter (`$lte` on `expires_at_ts`, inside `$and` for one source). That filter matched nothing so far, and `delete` has not run for real. The retriever's filters (`$in`, `$gt`, `$and`) and the `embeddings` option were checked against an in-memory ChromaDB 0.5.23 (the version this project pins), which also ran the expiry sweep end to end. They have not yet run against the ChromaDB container with the real embedding model. The unit tests use a fake store. Task 14 proves all of this against the real client.

## Open items

- Default `expires_at`: the adapters use 90 days for a static repository (`DEFAULT_EXPIRY`). The ingest command has no option to change it yet. Add one per source if a source needs it.
- **Share-alike licences** (CC-BY-SA) are refused for full text. Someone must decide whether we can accept them and what that means for our own content. To allow them, move their names from `NEEDS_DECISION` to `ALLOWED_FULL_TEXT` in `licence.py`.
- The GitHub MCP server was confirmed on a real run on 2026-11-19 (image tag, `stdio` subcommand, response shape, folder listing). Stdio is refused in production, so ingest runs from a development or CI machine until the server is hosted over HTTP. See `docs/architecture/mcp.md`, section 10.
- The `domain` list is a starting set. Phase 9 will extend it for non-technical roles.
- The retrieval defaults (cutoff 0.30, variety 0.7, 2 chunks per page) are starting values. Task 13 tunes them against the golden set.
- `search()` reads one collection per call and has no live fallback. Task 10 adds the fallback for empty results.

## Changelog

| Date | Change |
|---|---|
| 2026-11-10 | First version: schema, two-collection layout, embedding model kept |
| 2026-11-18 | Added Decision 4: pipeline, licence gate and source adapters (tasks 5 to 7). Corrected the update rules to match the code. Updated status and open items |
| 2026-11-19 | Added Decision 5: the ingest command and expiry sweep (task 8). Updated the status, the source checklist and the open items |
| 2026-11-19 | Recorded the first real ingest of both sources (45 Handbook pages, 41 roadmap.sh links) and what it confirmed about the GitHub server and ChromaDB |
| 2026-11-19 | Added Decision 6: retrieval (task 9). Marked task 9 built, removed the `RetrieverAgent` finding, and added two open items |