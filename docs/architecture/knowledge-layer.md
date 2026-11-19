# Knowledge Layer Design

How AI Talent Hub stores and finds reference knowledge (interview material, learning resources) after the Phase 7 rebuild. This covers the schema, the collection layout, the embedding model, how the pipeline, the licence gate and the source adapters fill the collections, how the retriever finds content, what happens when it finds nothing, and how the interview and learning-plan agents use all of that.

**Tracker:** Phase 7, tasks 2 to 12. **Related:** `docs/architecture/mcp.md`, `docs/mcp/vetting-log.md`, `docs/attribution.md`, `.claude/skills/content-source-vetting/SKILL.md`.
**Status:** design agreed 2026-11-10. The schema, collections, ingestion pipeline, licence gate, two source adapters, and the ingest command with its expiry sweep are built and unit tested (updated 2026-11-19). Both sources were ingested for real on 2026-11-19 (see Decision 5). The retriever can search them (task 9, Decision 6), and `retrieve_with_fallback()` says when to answer from general knowledge instead (task 10, Decision 7). The interview and roadmap agents now read the new collections through it and return the sources they used (task 11, Decision 8). Learning links now come from three link-only lists (roadmap.sh, official documentation and freeCodeCamp). Every ingest checks them for dead pages, and the roadmap agent ranks resources by source trust and freshness (task 12, Decision 9). The two new lists passed a real link check on 2026-11-19 (108 of 108 links answering). A real ingest of them is still to do.

---

## Decisions at a glance

| # | Decision | Choice |
|---|---|---|
| 1 | What one piece of knowledge looks like | A `KnowledgeDoc` record. Every chunk carries its source, licence and dates as flat metadata |
| 2 | Collections | Two new collections, `interview_knowledge` and `learning_knowledge`, filtered by metadata. `skills_taxonomy` is reserved for Phase 9 |
| 3 | Embedding model | **Keep `all-MiniLM-L6-v2` (English only).** Decided 2026-11-10 |
| 4 | Old data | Not migrated. It stays in the local snapshot and is deleted in task 15 after the new evaluation passes |
| 5 | Licence policy | Allowlist of permissive licences for full text. Share-alike, unknown and restrictive licences are refused. Restrictive sources are link-only |
| 6 | Where content comes from | One adapter per source. Tech Interview Handbook (full text), and three link-only lists: roadmap.sh, official documentation and freeCodeCamp (task 12) |
| 7 | How content is loaded and kept fresh | One command (`python -m app.ingest.run`). Link-only lists are checked for dead pages first. After each run, chunks of that source that are past `expires_at` are removed |
| 8 | How content is found | `RAGRetriever.search()`: filters in ChromaDB, skips expired chunks, drops weak matches, avoids near-duplicates, returns citations. Details in Decision 6 below |
| 9 | What happens when nothing is found | `retrieve_with_fallback()`: the answer comes from the model's own knowledge, is labelled as such, and shows no sources. No web search and no outside service. Details in Decision 7 below. Decided 2026-11-19 |
| 10 | How the agents use it | Both agents call `retrieve_with_fallback()` in a worker thread, put the text into the prompt as untrusted data, and return `sources` and `notice`. A link in a learning plan must come from a retrieved page. Details in Decision 8 below |
| 11 | How learning links are kept healthy and ranked | Link lists are checked for dead pages on every ingest (404 and 410 are dropped, anything unclear is kept). The roadmap agent ranks resources by similarity, nudged by source trust and freshness. Details in Decision 9 below |

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

**Why new names instead of reusing `learning_resources`:** the old agents kept working until they were rewired in task 11, and task 15 can delete the old five collections cleanly without any risk to the new ones. This differs from the example names in the tracker for that reason only.

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
| Official documentation | `link_only.py` with `data/official_docs.yaml` | link only | `proprietary` (links only) | Our own YAML list | 44 links with descriptions written by us (task 12) |
| freeCodeCamp | `link_only.py` with `data/freecodecamp.yaml` | link only | `proprietary` (links only) | Our own YAML list | 23 links: courses, YouTube channel and playlists, news topics (task 12) |

**Reading files.** Adapters read through a small `SourceReader` interface. In production that is `GatewayGitHubReader` (`readers.py`), which goes through the MCP gateway and refuses a failed call, old cached data, a result cut at `max_result_chars`, and an empty reply. In tests it is an in-memory reader, so the adapters run with no network.

**Handbook cleaning.** Before storing, the adapter removes page headers, imports, components, HTML and images; links keep their text but lose the address; paragraphs, list items and table rows with affiliate links or sales wording are removed. Code blocks and inline code are never changed. A page that has under 200 characters left is skipped. It skips `resume-old.md`, `best-coding-interview-courses.md`, `mock-interviews.md` and template files, and records the reason for each. Each page is classified into a `content_type`, `role_family` and `level` from its file name, so behavioural pages go to `interview_knowledge` and the study plan goes to `learning_knowledge`.

**Link-only lists.** Each entry needs a URL, title, domain, role family and a one-sentence summary. A bad or repeated entry is skipped with a reason and the rest still load. The text stored is the title and the summary. Every address is tidied first (`normalise_url()`: `https`, lower-case host, no fragment, no tracking parameters, no trailing slash), so one page has one address and one document id. A page already listed, in the same file or by an earlier list in the same run, is skipped with the reason.

**Credit.** Each source's credit line and licence notice are recorded in `docs/attribution.md`.

### Adding a source

1. Vet it and record the verdict in `docs/mcp/vetting-log.md`.
2. Read its licence file itself.
3. Full text: add an adapter in `app/ingest/sources/` that returns a `SourceBatch`. Link only: add a YAML list and use `LinkOnlyAdapter`.
4. Register it in `SOURCES` in `app/ingest/run.py`. List any MCP server it needs in `servers`. A link-only list is registered with `link_only=True`, so the link check runs on it, and it needs an entry in `SOURCE_TRUST` (Decision 9). A test fails if either is missing.
5. Add it to `docs/attribution.md`.
6. Add tests. Run `python -m app.ingest.run --source <name> --dry-run`, then a real run, and check the stored credit line.

---

## Decision 5: the ingest command and expiry (task 8)

Code: `app/ingest/run.py` (the command) and `app/ingest/expiry.py` (finding and removing expired chunks).

```
python -m app.ingest.run --source tech-interview-handbook --enable-server github
python -m app.ingest.run --source roadmap-sh
python -m app.ingest.run --source official-docs
python -m app.ingest.run --source freecodecamp
python -m app.ingest.run --source all --dry-run
```

Run it from `backend/`. ChromaDB must be running (`CHROMA_HOST` and `CHROMA_PORT` in `backend/.env`).

### What one run does

For each chosen source, in order:

1. Ask the source adapter for its documents.
2. Link-only sources only: check each link (Decision 9). A page the site says is gone is left out, and any copy stored earlier is removed.
3. Give the documents to the pipeline. (A dry run only checks the licence gate.)
4. Remove that source's chunks that are past their expiry date.
5. Print a report: fetched, added, updated, unchanged, refused, skipped, failed and expired, with the reason for anything refused, skipped or failed.

A source that cannot be read is reported as `PROBLEM`. The other sources still run.

### Options

| Option | Effect |
|---|---|
| `--source NAME` or `--source all` | Required. `NAME` is `tech-interview-handbook`, `roadmap-sh`, `official-docs` or `freecodecamp` |
| `--dry-run` | Fetch, check links and licences, and list expired chunks. Writes and deletes nothing, and creates no collections. It still sends the link-check requests, which only read |
| `--no-expire` | Do not remove expired chunks |
| `--no-link-check` | Do not ask the sites whether link-only pages still exist |
| `--enable-server NAME` | Switch an MCP server on for this run only |

Exit code: `0` when nothing failed, `1` when a document or a whole source failed (a refused document is not a failure, and neither is a broken link), `2` for a bad argument or an unknown server name. `--source all` includes the handbook, so it exits `1` while the `github` server is off or has no token.

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
result.chunks      # in MMR order, the first is the best match: .text, .similarity, .metadata, .citation
result.citations   # one {title, url, licence, attribution} per page
result.empty       # True when nothing usable was found
```

`search()` is the way to read the two knowledge collections. The interview and roadmap agents moved off the old `retrieve()` in task 11. It stays only until task 15, which checks for any remaining caller and removes it together with the old collections. Agents should not call `search()` directly. They call `retrieve_with_fallback()` (Decision 7), which wraps it.

### What one search does

1. Check the arguments. A typo is an error, not an empty result.
2. Build the filter (below) and ask ChromaDB for several times `top_k` candidates (4 times, and at least 20), because some will be dropped.
3. Drop candidates below the similarity cutoff.
4. Pick `top_k` of the rest with MMR: the first pick is the best match and each next one adds variety, with at most 2 chunks from one page.
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

Empty with `candidates` 0 means nothing matched the filters (for example a domain with no content). Empty with `below_cutoff` above 0 means chunks matched, but weakly. The fallback (Decision 7) and the empty-retrieval metric use this.

`chunks` is in the order MMR picked them. The first is the best match. Each next one trades a little relevance for variety, so a later chunk can score lower than one that was left out. With `mmr=False` the order is plain best first.

### Why these choices

- Similarity is `1 - distance`. That is correct because the collections use cosine distance.
- MMR is there because the handbook pages overlap. Without it the top 5 can be five near-copies of one idea.
- The per-page limit stops one long page from filling the whole result.
- The ranking maths is plain Python. It only ever handles about 20 vectors, so it needs no numpy.
- ChromaDB returns the stored vectors on request (`include_embeddings`), so nothing is embedded a second time.

### What it does not do

- It searches one collection per call. A caller that wants both makes two calls.
- It does not decide what to do about an empty result. `retrieve_with_fallback()` does (Decision 7).
- It is vector search only (no keyword search), and English only, because of the embedding model.

---

## Decision 7: when nothing is found (task 10)

Code: `app/rag/fallback.py` (`retrieve_with_fallback()`).

```python
retrieval = retrieve_with_fallback(
    "tell me about a time you led a project",
    "interview_knowledge",
    role_family="technical",
    level="mid",
)
retrieval.grounded      # True when the answer can rest on reviewed chunks
retrieval.route         # "knowledge" or "general_knowledge"
retrieval.reason        # why it fell back (empty on the knowledge route)
retrieval.chunks        # the chunks, only on the knowledge route
retrieval.citations     # one {title, url, licence, attribution} per page, only on the knowledge route
retrieval.prompt_note   # text for the model's prompt (empty when grounded)
retrieval.user_note     # text to show the person (empty when grounded)
```

The options after the collection name go straight to `RAGRetriever.search()`.

### The decision

**When our own knowledge has nothing to offer, the answer comes from the model's own knowledge, is labelled as such, and shows no sources.** There is no web search, no live lookup and no outside service. Decided by the project owner on 2026-11-19.

This replaces the original plan for task 10, which was a live web lookup on an empty result. Why it was dropped:

| Option looked at | Why not |
|---|---|
| Brave Search MCP | It no longer has a free plan (the dashboard on 2026-11-19 showed a prepaid Search plan at $5.00 per 1,000 requests). Its output format is not documented, so it could not be proven without paying |
| Tavily MCP | Its terms let Tavily and its AI providers use queries and outputs to train models. It is only a backup in the vetting log |
| Google Programmable Search | Closed to new customers and shutting down on 2027-01-01 |
| Groq `browser_search` | It works with the model we use, but its cost, output format and rules on storing results are not documented. It cannot be combined with structured outputs. Not tested |
| Scraping a search engine | Fragile and against the terms of the engines |

The owner chose not to pay for a search service or to rely on an outside one. Everything in the table stays possible later, but none is planned. See the open items.

### The two routes

| Route | When | What the caller gets |
|---|---|---|
| `knowledge` | At least one reviewed chunk matched | The chunks and their citations. No note |
| `general_knowledge` | Nothing usable matched, or retrieval failed | No chunks and no citations. A note for the prompt and a note for the person |

The reason on the general route:

| Reason | Meaning |
|---|---|
| `no_match` | Nothing passed the filters (for example a domain with no content) |
| `weak_match` | Chunks passed the filters, but all were below the similarity cutoff |
| `empty_query` | There was nothing to search for |
| `retrieval_error` | The vector store or the embedding model failed |

### The two notes

- **Prompt note (for the model):** "No reviewed source matched this request. Answer from general knowledge only. Do not invent sources, links or quotes, and do not present the answer as based on reviewed material."
- **User note (for the screen):** "Not based on our reviewed sources. This comes from general knowledge, so check it before relying on it."

Both are first-version wording. Task 11 puts the first into the prompt and returns the second as `notice`. Task 16 shows it on the screen.

### Failure handling

- **A retrieval failure never breaks an interview or a roadmap.** If the vector store or the embedding model raises, the error is logged with its traceback and the route is `general_knowledge` with the reason `retrieval_error`.
- **A bad argument still raises.** A typo in `role_family`, `level` or `content_type`, or a collection name that is not a knowledge collection, raises `ValueError`, exactly as `search()` does. That is a bug in the calling code, so hiding it would be worse. It is raised even when the query is blank.

### Logging

One line per call: `fallback route=... reason=... collection=... candidates=... below_cutoff=...`. It has counts only and never the query text, so gaps in the knowledge base can be counted without writing anyone's words to the logs. Task 13 and the tracing work read this to measure how often the fallback happens.

### What it does not do

- It adds nothing to any collection and stores nothing. A general-knowledge answer is never saved as knowledge.
- It does not check that the model obeyed the prompt note. The golden set (task 13) tests that answers on the general route carry no invented sources.
- It is synchronous, like `search()`. The agents call it from a worker thread (Decision 8).

---

## Decision 8: how the agents use it (task 11)

Code: `app/agents/candidate/roadmap_agent.py`, `app/agents/candidate/interview_agent.py`, `app/llm/prompts/roadmap_prompts.py`, `app/services/interview_service.py`, `app/graphs/candidate_graph.py` and `SourceItem` in `app/database/schemas.py`. The endpoints are in `docs/api/api-reference.md`.

| | Roadmap agent | Interview agent |
|---|---|---|
| Collection | `learning_knowledge` | `interview_knowledge` |
| Search | One per skill gap: "learning resources for {skill}", top 3 each. Merged, deduplicated by chunk, ranked by similarity with a nudge for source trust and freshness (Decision 9), at most 12, and at most 12 skills searched | One: "{company} {role} interview questions", top 5 |
| Prompt size | 500 characters per chunk | 3000 characters in all, at most 700 per chunk. A chunk is included whole up to its cap or left out |
| Filters | None | None |
| `sources` returned | The reviewed pages the plan actually links to | The reviewed pages that went into the prompt |
| `notice` returned | When the plan has weeks but links to no reviewed page | When no reviewed material was used |
| Where it reaches the caller | `GET /api/candidate/learning-plan` | `POST /api/candidate/interview/start` only |

**Rules both agents follow**

- **Retrieval runs in a worker thread.** `search()` is synchronous (embedding and a Chroma call), so calling it from an async agent blocked the event loop. The old roadmap agent did exactly that.
- **Retrieved text is data.** It goes into the prompt through `wrap_untrusted()`, so a page cannot close the data block early or give the model orders. If nothing matched, the prompt gets the fallback's prompt note instead.
- **A retrieval failure never breaks the agent.** Any error, including a bad argument, is logged and the agent carries on with the general-knowledge route. The roadmap agent also carries on when only one skill's search fails.
- **A canned reply claims nothing.** If the model fails and the agent uses a standard opening question, `sources` is empty and `notice` is `null`, because the question is not based on anything retrieved. The same holds for a roadmap that could not be generated.

**Links in a learning plan.** The model may only use links from the pages that were retrieved for this request.
- A link that was retrieved gets that page's real title, so a wrong title cannot sit on a real link.
- A link that was not retrieved is made up, and its entry is dropped.
- An entry with no link is a generic suggestion ("Official documentation") and is kept.
- Each resource is `{title, url, type}`. The old `source` key is gone.

**What `sources` means.** For a learning plan it is exact: the pages the plan links to. For an interview it is looser. The model is told to use the material for flavour and not to quote it, so `sources` lists the pages that could have shaped the opening question, not a claim about any sentence in it.

**What is not stored.** The interview `sources` and `notice` are returned once, with the first question. They are not saved in the session row or in Redis, and the later questions come from the transcript, not from reference material.

**Known limit: no role filter.** Neither agent filters on `role_family`, because the agents do not yet know a role's family. `interview_knowledge` holds only software-engineering content, so a non-technical role can get weakly related chunks above the cutoff. The golden set (task 13) measures how often, and Phase 9 adds a role-to-family mapping.

---

## Decision 9: learning links, link health and ranking (task 12)

Code: `app/ingest/linkcheck.py`, `app/ingest/sources/link_only.py`, `app/ingest/run.py`, the lists in `app/ingest/sources/data/`, and `RoadmapAgent` in `app/agents/candidate/roadmap_agent.py`.

A learning plan is only as good as its links. This task makes three changes: more good links, a check that they still work, and a better order for the ones the plan sees.

### The lists

| List | Links | What it holds |
|---|---|---|
| `roadmap_sh.yaml` | 41 | Role roadmaps (task 6) |
| `official_docs.yaml` | 44 | Front pages of documentation published by the makers of each tool, language or platform. 38 for technical roles, 4 for non-technical roles, 2 for all roles |
| `freecodecamp.yaml` | 23 | 11 course pages, 2 YouTube links (channel and playlists), 10 news topic pages |

All three are link-only: a link, a title and one sentence we wrote. No MCP fetch is used for content, because nothing is copied. Each list has its own `source_id` and its own record in `docs/mcp/vetting-log.md`. Only top pages are listed, never single articles or videos, because deep pages move and a page's links cannot be confirmed from outside. If someone has watched a video and written the sentence, it can be added.

### The link check

`check_links()` in `app/ingest/linkcheck.py` asks each site whether a page is still there. It uses its own small `httpx` client, not the MCP Fetch server, because it needs only a status code. It reuses the gateway's URL guard (`validate_url`): `https` or `http` only, no credentials, the host must be one of the hosts in the list, and every address it resolves to must be public.

- It sends `HEAD`. When `HEAD` is refused or says the page is gone (400, 403, 404, 405, 410, 501), it sends a one-byte `GET`, and the `GET` answer counts. This matters: the first real run showed `support.google.com` answering `HEAD` with 404 for a page that exists.
- It follows up to 3 redirects by hand, running each hop through the guard. A redirect to another host is not followed, and the report says where it led, so the list can be updated.
- It sends 5 requests at a time, one at a time per host with a 0.25 s pause, with a 10 s timeout and a User-Agent that names the project. No cookies and no user data go out.

| Result | When | What ingest does |
|---|---|---|
| `ok` | The final answer is 2xx | Keeps the link |
| `broken` | `HEAD` and `GET` both say 404 or 410 | Leaves the link out, and removes any copy stored earlier |
| `unknown` | Anything else: timeout, 403, 429, 5xx, a redirect to another site, no network, a blocked address | Keeps the link |

A link is dropped only when the site clearly says the page is gone. A bot block or a bad network day never deletes good links. Removing the stored copy matters: a page that was fine on an earlier run and is dead now would otherwise keep appearing in plans until its 90 days ran out. A dry run reports what would be removed and changes nothing. If the check as a whole crashes, nothing is dropped. A broken link is reported but does not change the exit code.

The check runs for link-only sources, not for the handbook. The handbook is read from its repository on every run, so a page removed upstream stops being refreshed and expires. Links inside the handbook's text are not checked.

### First real run (2026-11-19)

All 108 links answered in the end. The first run found two problems, both fixed:

1. `support.google.com/analytics` looked broken, because the server answers `HEAD` with 404. The checker now confirms a 404 or 410 with a `GET`.
2. `docs.claude.com` redirects to `platform.claude.com`. The checker rightly did not follow a redirect to another host and kept the link as `unknown`. The address in the list was updated, and the report now says where a link was redirected to.

### Ranking

Before the cap of 12, the roadmap agent ranks every retrieved chunk:

```
rank = similarity x (0.6 + 0.3 x trust + 0.1 x freshness)
```

- **Trust** comes from `SOURCE_TRUST`: official-docs 1.0, roadmap-sh 0.8, freecodecamp 0.8, tech-interview-handbook 0.7, any other source 0.5.
- **Freshness** is 1 for a page confirmed just now and falls evenly to 0 at 90 days, the same as the expiry. It is read from `fetched_at_ts`, which every ingest run refreshes, so it says when the page was last confirmed. A missing or odd date is neutral (0.5).
- Similarity stays the main term, so trust and freshness only separate near ties. An official page beats a list match that is about 0.04 better, never a clearly better one. Nothing changes in the stored data.
- **Coverage.** Every skill gap with a match keeps its best chunk before any skill gets a second one. Up to 12 skills are searched (the old loop stopped after 12 chunks, so later skills were never searched). The prompt still shows the real similarity.

The weights and trust values are starting values. Task 13 tunes them against the golden set.

### What it does not do

- It does not detect a "soft 404" (a normal page that says "not found"), or a page that answers 200 but no longer says what our sentence says.
- A site that blocks bots can hide a dead link, because blocked answers are kept.
- The check is run by hand for now. Running it on a schedule is a later CI task.
- Non-technical roles are thin: official documentation covers them poorly (4 of the 44 links are for non-technical roles and 2 for all roles). Phase 9 needs more sources for them.
- The trust values are a judgement. The list includes cloud vendors and Anthropic's own prompt guide, so it is not vendor-neutral.

---

## What the rest of Phase 7 builds on this

| Task | Uses |
|---|---|
| 5 | **Built.** Pipeline writes `KnowledgeDoc` chunks with deterministic ids through `upsert_documents` |
| 6, 7 | **Built.** Source adapters fill the fields. The licence gate checks `licence`, `attribution` and `ingest_mode` |
| 8 | **Built.** `python -m app.ingest.run` runs the adapters and the pipeline. Refresh uses `content_hash`; expiry uses `expires_at_ts`. Both sources ran for real on 2026-11-19 |
| 9 | **Built.** Retriever v2 (`RAGRetriever.search()`) filters on the fields above, skips expired chunks, and returns `title`, `url`, `licence` and `attribution` as citations |
| 10 | **Built.** `retrieve_with_fallback()` (Decision 7). The original live web lookup was dropped on 2026-11-19 |
| 11 | **Built.** The interview and roadmap agents call `retrieve_with_fallback()` and return `sources` built from the citations, plus `notice` (Decision 8). The prompt note goes into the prompt on the general route. A learning plan can only link to retrieved pages |
| 12 | **Built.** Three link-only lists (108 links), a link-health check on every link ingest, and ranking by trust and freshness (Decision 9). The credit is shown by task 16 |
| 13, 14 | The evaluation set and tests cover the filters, idempotency, the licence gate and the fallback (including that a general-knowledge answer carries no invented sources) |

---

## Findings from reading the code

- `RetrieverAgent` (`agents/shared/retriever_agent.py`) was used nowhere and could not have worked: it `await`ed a function that is not async and passed `collection=` where the retriever expects `collection_name=`. Removed in task 9.
- The real ingest runs on 2026-11-19 worked with ChromaDB's `upsert`, `get` with `where`, and `update` calls, and ChromaDB accepted the expiry filter (`$lte` on `expires_at_ts`, inside `$and` for one source). That filter matched nothing so far, and `delete` has not run for real. The retriever's filters (`$in`, `$gt`, `$and`) and the `embeddings` option were checked against an in-memory ChromaDB 0.5.23 (the version this project pins), which also ran the expiry sweep end to end. They have not yet run against the ChromaDB container with the real embedding model. The unit tests use a fake store. Task 14 proves all of this against the real client.
- The old roadmap agent called the synchronous `retrieve()` straight from an async method, which blocked the event loop for the length of every search. Fixed in task 11 (Decision 8).
- The roadmap agent stopped searching once it had 12 chunks, so with five or more skill gaps the later skills got no search. Fixed in task 12 (Decision 9).
- The first link check treated a `HEAD` 404 as proof that a page was gone. The first real run showed a site that answers `HEAD` with 404 for a page that loads. Fixed in task 12.

## Open items

- Default `expires_at`: the adapters use 90 days for a static repository (`DEFAULT_EXPIRY`). The ingest command has no option to change it yet. Add one per source if a source needs it.
- **Share-alike licences** (CC-BY-SA) are refused for full text. Someone must decide whether we can accept them and what that means for our own content. To allow them, move their names from `NEEDS_DECISION` to `ALLOWED_FULL_TEXT` in `licence.py`.
- The GitHub MCP server was confirmed on a real run on 2026-11-19 (image tag, `stdio` subcommand, response shape, folder listing). Stdio is refused in production, so ingest runs from a development or CI machine until the server is hosted over HTTP. See `docs/architecture/mcp.md`, section 10.
- The `domain` list is a starting set. Phase 9 will extend it for non-technical roles.
- The retrieval defaults (cutoff 0.30, variety 0.7, 2 chunks per page) are starting values. Task 13 tunes them against the golden set.
- **Live help for gaps (not planned).** Two ideas stay possible if the golden set shows the general-knowledge answers are too weak: a short list of trusted sites fetched live (our own topic-to-page table, nothing stored), or a test of Groq's `browser_search` for its cost, output format and storage rules. Each needs vetting first, and neither is scheduled.
- The wording of the two fallback notes is a first version. Revisit it in task 16, when the screen shows the notice.
- **No role filter in the agents (known limit).** See Decision 8. Non-technical roles may get weak software chunks until Phase 9 adds a role-to-family mapping. Task 13 measures it.
- **The interview prompt still says "Reference material from real interviews".** The knowledge base holds preparation guides, not real interviews, so the wording is a little off. Fix it when the prompt is next touched.
- **The frontend still reads the old resource `source` key.** `AnalysisView.tsx` uses it only as a tooltip. Task 16 updates it and shows `sources` and `notice`.
- **The context budgets are starting values.** 12 resources and 500 characters per chunk for the roadmap; 3000 characters and 700 per chunk for the interview. Task 13 can tune them.
- **The agents have not run against the real ChromaDB.** Their unit tests use a stand-in retriever. Task 14 runs them against the real client with the learning chunks (60 before the new lists are ingested) and the 829 interview chunks.
- **The two new lists are not ingested yet.** They passed a real link check, but the real ingest (`--source official-docs`, then `--source freecodecamp`) is still to run.
- **The link check is manual.** Run `python -m app.ingest.run --source <name> --dry-run` after any list change. A scheduled run belongs with CI.
- **The `github` token is not set on the development machine.** The handbook cannot be re-ingested until `GITHUB_PERSONAL_ACCESS_TOKEN` is in `backend/.env`. It is to be set before the last task of Phase 7. Until then, run the link sources one at a time, not with `--source all`.
- **Links inside the handbook's text are not checked.** Only the link-only lists get the check.
- **freeCodeCamp's terms, its YouTube channel's terms and the licence of its news articles are unread.** This has no effect while we only link (vetting log).
- **Ranking weights and trust values are starting values.** Task 13 tunes them. Non-technical roles are thin in the official-documentation list until Phase 9.

## Changelog

| Date | Change |
|---|---|
| 2026-11-10 | First version: schema, two-collection layout, embedding model kept |
| 2026-11-18 | Added Decision 4: pipeline, licence gate and source adapters (tasks 5 to 7). Corrected the update rules to match the code. Updated status and open items |
| 2026-11-19 | Added Decision 5: the ingest command and expiry sweep (task 8). Updated the status, the source checklist and the open items |
| 2026-11-19 | Recorded the first real ingest of both sources (45 Handbook pages, 41 roadmap.sh links) and what it confirmed about the GitHub server and ChromaDB |
| 2026-11-19 | Added Decision 6: retrieval (task 9). Marked task 9 built, removed the `RetrieverAgent` finding, and added two open items |
| 2026-11-19 | Added Decision 7: the fallback when nothing is found (task 10). The live web lookup was dropped, so no search provider is used. Fixed the wording on result order in Decision 6 and updated the open items |
| 2026-11-19 | Added Decision 8: how the agents use the knowledge layer (task 11). The interview and roadmap agents now read the new collections through `retrieve_with_fallback()` and return `sources` and `notice`. Marked task 11 built, updated the status and the task table, and added five open items and one finding |
| 2026-11-19 | Added Decision 9: learning links, link health and ranking (task 12). Two link lists added, the link check built and run for real (108 of 108 links answering), and resources ranked by trust and freshness. Updated the status, the adapter table, the ingest options and the open items |