# Knowledge Layer Design

How AI Talent Hub stores and finds reference knowledge (interview material, learning resources) after the Phase 7 rebuild. This covers the schema, the collection layout, the embedding model, how the pipeline, the licence gate and the source adapters fill the collections, how the retriever finds content, what happens when it finds nothing, and how the interview and learning-plan agents use all of that.

**Tracker:** Phase 7, tasks 2 to 15. **Related:** `docs/architecture/mcp.md`, `docs/mcp/vetting-log.md`, `docs/attribution.md`, `.claude/skills/content-source-vetting/SKILL.md`.
**Status:** design agreed 2026-11-10. The schema, collections, ingestion pipeline, licence gate, two source adapters, and the ingest command with its expiry sweep are built and unit tested (updated 2026-11-19). Both sources were ingested for real on 2026-11-19 (see Decision 5). The retriever can search them (task 9, Decision 6), and `retrieve_with_fallback()` says when to answer from general knowledge instead (task 10, Decision 7). The interview and roadmap agents now read the new collections through it and return the sources they used (task 11, Decision 8). Learning links now come from three link-only lists (roadmap.sh, official documentation and freeCodeCamp). Every ingest checks them for dead pages, and the roadmap agent ranks resources by source trust and freshness (task 12, Decision 9). The two new lists passed a real link check on 2026-11-19 (108 of 108 links answering) and were ingested for real on 2026-11-23 (44 and 23 chunks, so `learning_knowledge` holds 127). The golden evaluation set is built and has had its first runs (task 13, Decision 10). It was run against the real system on 2026-11-24. Two settings were changed because of it: the retrieval cutoff went from 0.30 to 0.40 and freeCodeCamp's trust from 0.8 to 0.7 (Decision 10). The results of that run are recorded as the starting scores. The labels are still unchecked drafts, so `v1` is not frozen. Integration tests now run the retriever, the pipeline and both agents against the real ChromaDB (task 14). The five old collections, the old `retrieve()` and the Phase 2 evaluation set were removed in task 15 (2026-11-24).

---

## Decisions at a glance

| # | Decision | Choice |
|---|---|---|
| 1 | What one piece of knowledge looks like | A `KnowledgeDoc` record. Every chunk carries its source, licence and dates as flat metadata |
| 2 | Collections | Two new collections, `interview_knowledge` and `learning_knowledge`, filtered by metadata. `skills_taxonomy` is reserved for Phase 9 |
| 3 | Embedding model | **Keep `all-MiniLM-L6-v2` (English only).** Decided 2026-11-10 |
| 4 | Old data | Not migrated. The five old collections were deleted from ChromaDB in task 15 (2026-11-24), after the first evaluation run. Their text and metadata exist only in the local snapshot |
| 5 | Licence policy | Allowlist of permissive licences for full text. Share-alike, unknown and restrictive licences are refused. Restrictive sources are link-only |
| 6 | Where content comes from | One adapter per source. Tech Interview Handbook (full text), and three link-only lists: roadmap.sh, official documentation and freeCodeCamp (task 12) |
| 7 | How content is loaded and kept fresh | One command (`python -m app.ingest.run`). Link-only lists are checked for dead pages first. After each run, chunks of that source that are past `expires_at` are removed |
| 8 | How content is found | `RAGRetriever.search()`: filters in ChromaDB, skips expired chunks, drops weak matches, avoids near-duplicates, returns citations. Details in Decision 6 below |
| 9 | What happens when nothing is found | `retrieve_with_fallback()`: the answer comes from the model's own knowledge, is labelled as such, and shows no sources. No web search and no outside service. Details in Decision 7 below. Decided 2026-11-19 |
| 10 | How the agents use it | Both agents call `retrieve_with_fallback()` in a worker thread, put the text into the prompt as untrusted data, and return `sources` and `notice`. A link in a learning plan must come from a retrieved page. Details in Decision 8 below |
| 11 | How learning links are kept healthy and ranked | Link lists are checked for dead pages on every ingest (404 and 410 are dropped, anything unclear is kept). The roadmap agent ranks resources by similarity, nudged by source trust and freshness. Details in Decision 9 below |
| 12 | How we know a change made things worse | A golden evaluation set of hand-checked cases (37 for retrieval, 26 for learning plans), run on demand against the real system and compared with a saved baseline. Tune on the `dev` cases and confirm on `test`. Details in Decision 10 below. First run recorded 2026-11-24 |

---

## Where we start

Facts from the repo and the snapshot taken on 2026-11-10, before the rebuild. The five collections described here no longer exist (task 15):

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

Five topic-based collections are replaced by two content-based ones. Topic, role and level become filters, so adding a new domain needs no new collection. The code is `app/rag/collections.py`, which lists only the two new collections. The old five were removed from it, and from ChromaDB, in task 15.

| Collection | Holds | `content_type` values |
|---|---|---|
| `interview_knowledge` | What the interview coach draws on | `interview_question`, `behavioral_question`, `experience`, `company_pattern`, `concept_note` |
| `learning_knowledge` | What the learning plan draws on | `course`, `article`, `video`, `roadmap`, `docs` |
| `skills_taxonomy` (reserved) | Occupations and skills. Created in Phase 9 | n/a |

Searches filter on `domain`, `role_family`, `level` and `content_type`, and always on `schema_version = 2`.

**Why new names instead of reusing `learning_resources`:** the old agents kept working until they were rewired in task 11, and task 15 could delete the old five collections cleanly without any risk to the new ones. This differs from the example names in the tracker for that reason only.

**Old data:** the 119 old chunks have no recorded source or licence, so under the rule above they cannot be loaded into the new collections. They were deleted from ChromaDB in task 15 (2026-11-24), after the first run of the evaluation set (Decision 10) showed the new collections working. A snapshot with their text and metadata is kept in `data/snapshots/pre-rag-rebuild-20261110T064200Z`. That folder is git-ignored, so it exists only on the machine that made it, and it holds no embeddings. Nothing restores it automatically.

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

A run is `ok` unless a document `failed`. The collections must exist first (`python scripts/init_chroma.py`). The pipeline uses four `VectorStore` methods added for it: `upsert_documents`, `get_by_where`, `update_metadatas` and `delete_ids`. The old `add_documents()`, which used random ids and so could store the same page twice, was removed in task 15.

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
- Only the chunks of the source being run are removed, in both knowledge collections. Running `roadmap-sh` never removes handbook content. Collections other than the two knowledge collections are never touched.
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

`search()` is the way to read the two knowledge collections. The interview and roadmap agents moved off the old `retrieve()` in task 11, and task 15 removed it, together with `retrieve_across_collections()`, `add_documents()` and the old collections. `search()` refuses any collection name other than the two knowledge collections. Agents should not call `search()` directly. They call `retrieve_with_fallback()` (Decision 7), which wraps it.

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
| `min_similarity` | 0.40 | Below this a match is noise. It is the cutoff the agents use. It was 0.30 until task 13 (Decision 10) |
| `mmr` | on | Pick varied results. `mmr=False` gives plain best-first |
| `mmr_lambda` | 0.7 | 1.0 means only relevance, 0.0 means only variety |
| `max_per_doc` | 2 | Most chunks one page may supply. `None` or `0` switches it off |
| candidates fetched | 4 x `top_k`, at least 20 | Extra candidates so the cutoff and variety have something to choose from |

The cutoff was tuned in task 13 (0.30 to 0.40). The other values are still starting values.

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

### First real ingest (2026-11-23)

`official-docs` added 44 chunks and `freecodecamp` added 23. With the 41 roadmap.sh chunks and 19 chunks from the handbook's "Coding interview study plan" (content type `roadmap`, role family technical), `learning_knowledge` now holds 127 chunks. `interview_knowledge` holds 829.

### Ranking

Before the cap of 12, the roadmap agent ranks every retrieved chunk:

```
rank = similarity x (0.6 + 0.3 x trust + 0.1 x freshness)
```

- **Trust** comes from `SOURCE_TRUST`: official-docs 1.0, roadmap-sh 0.8, freecodecamp 0.7, tech-interview-handbook 0.7, any other source 0.5. (freeCodeCamp was 0.8 until task 13.)
- **Freshness** is 1 for a page confirmed just now and falls evenly to 0 at 90 days, the same as the expiry. It is read from `fetched_at_ts`, which every ingest run refreshes, so it says when the page was last confirmed. A missing or odd date is neutral (0.5).
- Similarity stays the main term, so trust and freshness only separate near ties. An official page beats a list match that is about 0.04 better, never a clearly better one. Nothing changes in the stored data.
- **Coverage.** Every skill gap with a match keeps its best chunk before any skill gets a second one. Up to 12 skills are searched (the old loop stopped after 12 chunks, so later skills were never searched). The prompt still shows the real similarity.

The three weights are still starting values. In task 13 the golden set found one weak spot (Docker, see Decision 10), and freeCodeCamp's trust was lowered from 0.8 to 0.7 to fix it.

### What it does not do

- It does not detect a "soft 404" (a normal page that says "not found"), or a page that answers 200 but no longer says what our sentence says.
- A site that blocks bots can hide a dead link, because blocked answers are kept.
- The check is run by hand for now. Running it on a schedule is a later CI task.
- Non-technical roles are thin: official documentation covers them poorly (4 of the 44 links are for non-technical roles and 2 for all roles). Phase 9 needs more sources for them.
- The trust values are a judgement. The list includes cloud vendors and Anthropic's own prompt guide, so it is not vendor-neutral.

---

## Decision 10: the golden evaluation set (task 13)

Code: `backend/evals/`. Data and a README for each set: `backend/evals/datasets/knowledge/` and `backend/evals/datasets/roadmap/`. Tests: `backend/tests/unit/evals/`.

A change to the cutoff, the ranking weights, a prompt, the model or a source can make answers worse without any test failing. The evaluation set is a small list of hand-checked cases that we run against the real system after such a change, and compare with a saved baseline. It follows the golden-set rules in the `eval-golden-set` skill.

| Set | File | Cases | The question it answers |
|---|---|---|---|
| Retrieval | `retrieval.v1.jsonl` | 37 (28 dev, 9 test) | Does search still find the right pages in `interview_knowledge` and `learning_knowledge`, and still return nothing when nothing fits? |
| Learning plan | `links.v1.jsonl` | 26 (19 dev, 7 test) | Does the plan link only to pages we retrieved, show its sources (or the "not based on reviewed sources" notice), rank the most trusted page first, and ignore instructions hidden in its input? |

### The cases

- One JSON object per line: `id`, `area`, `tags`, `split`, `input`, `expected`, `notes` (why the case exists), `source`, `added`, and `pair_with` for fairness pairs. `evals/cases.py` checks every file. An unknown key or a repeated id is an error, so a typo cannot quietly switch a check off.
- `input` is what the code under test receives: a query and a collection for retrieval, skill gaps and a target role for the plan.
- `expected` never holds a chunk id (ids change when the knowledge base is rebuilt) and never holds plan text (it differs on every run). Retrieval cases name keywords, a source id or a domain that a relevant page has, or say that nothing should come back. Plan cases name properties: how many links, which hosts, which page ranks first, which words must not appear.
- Every case has exactly one of the tags `technical` or `non-technical`, plus slice tags: `clear`, `borderline`, `messy`, `empty-input`, `no-match`, `regression`, `safety`, `fairness`, `interview`, `learning`, `ranking`, `coverage`, `fallback`.
- Fairness pairs are two cases that differ only in wording or letter case. They must name each other and sit in the same split. Safety cases hide instruction-like text in the input and name a canary word that must not appear in the plan.
- Cases hold invented people and roles only, and no text copied from a site.
- **All cases are `model-draft`**: Claude wrote them. They do not count until a person has checked the expected values and changed `source` to `reviewed-draft`. `python -m evals.cases --require-reviewed` fails while any draft is left.

### Splits

Each case is `dev` or `test` (about a quarter are `test`), fixed in the file. Tune on `dev`. Look at `test` only to confirm one chosen setting, once. The runners print a warning when a sweep is not on `dev`.

### What is measured

| Set | Measures |
|---|---|
| Retrieval | Precision (relevant results divided by results returned, so a cutoff that returns fewer chunks is not punished for it), hit rate, MRR, and how many "expect nothing" cases returned nothing |
| Learning plan | Pass or fail per check. Four checks run on every case: the model answered (not the fallback plan), no link was invented, every source has a link in the plan, and a plan never has both sources and a notice. Cases add checks for grounding, link count, link hosts, the best-ranked page and canary words |

Every report is given for all cases, each split and each tag, so a weak slice (non-technical roles, say) cannot hide in an average.

### Running it

The runners use the real ChromaDB and embedding model, and for plans the real Groq model. Nothing is faked, so they are not part of `pytest tests/unit`. From `backend/`:

```
python -m evals.cases                          # check the data files
python -m evals.run_retrieval --split dev
python -m evals.run_roadmap --split dev        # about 6 to 12 minutes, one model call per case
python -m evals.run_roadmap --repeat 3         # the model is not deterministic
```

A failing case is a finding, not an error, so the exit code stays 0. The exit code is 1 for a bad dataset or bad options, and 2 when a run could not be done (ChromaDB down, a search that raised). A search that failed is left out of the scores and listed, because counting it would make an "expect nothing" case pass for the wrong reason.

### Trying other settings

Options change one run only. Nothing is written to the app.

- Retrieval: `--min-similarity`, `--mmr-lambda`, `--max-per-doc`, and `--sweep NAME=v1,v2,...` for one row per value.
- Plans: `--ranking-only` runs only retrieval and ranking, calls no model, and checks only the cases that name a best page. It is fast, free and gives the same answer every time. With it, `--base-weight`, `--trust-weight`, `--fresh-weight`, `--source-trust SOURCE=VALUE` and `--sweep` try other ranking values.

When a value looks better on `dev`, run `test` once with it. Only then change the default in `app/rag/retriever.py` or `app/agents/candidate/roadmap_agent.py`, and record why here.

### Results, baseline and tolerance

- `--save` writes the run to `data/eval/results/<area>_v<N>_<time>.json`. `--compare FILE` prints which metrics moved, which cases started failing or passing, and which settings differ between the two runs, so a change in a number can be traced.
- The first reviewed run is copied beside its dataset as `retrieval.v1.baseline.json` or `links.v1.baseline.json`, and `v1` is then frozen. A frozen version is never edited. A change is a `v2` file with a line in the set's changelog. A label is never changed just to turn a run green.
- **A first baseline was measured on 2026-11-24 (see "Results of 2026-11-24" below), but `v1` is not frozen and no tolerance is agreed.** The labels are still `model-draft`, so the two result files were not copied to `*.v1.baseline.json`. Compare against them with `--compare` by file name. A tolerance (for example "no metric drops more than N points") comes from what the system really does. A drop of more than 0.05 on any retrieval metric or on the plan pass rate was suggested as a starting point, and it is not agreed.

### LangSmith

`backend/evals/langsmith_upload.py` copies a set to a LangSmith dataset (`ai-talent-hub-<area>-v<N>`), matching examples by case id. It does nothing unless `LANGSMITH_API_KEY` is set, never deletes, and `--dry-run` shows what it would send with no network. The files in the repository stay the source of truth. Phase 10 decides the final place for evaluation data.

### First runs (2026-11-23, `dev` split, no setting changed)

- **Retrieval:** 26 of 28 cases found a relevant page. Precision 0.61, hit rate 0.96, MRR 0.93. A sweep of the cutoff showed precision rising to 0.83 at 0.45 with hit rate and MRR unchanged. A cutoff near 0.40 looks safe, because it keeps a margin below the weakest best result. This is a candidate to confirm on `test`, not a change.
- **Learning plan (first 15 cases):** 9 of 11 `dev` cases passed. Both failures are the Docker pair (`rdm-003`, `rdm-015`): a freeCodeCamp topic page ranked above the official Get started page by a small margin. A higher trust weight or a lower freeCodeCamp trust fixes it. Eleven more ranking cases (including counter-cases where a community roadmap should win) were added before any weight is changed, so the choice does not rest on three scenarios.
- **Gaps seen:** the query `k8s` does not find Kubernetes pages (`ret-034`), and the handbook's study-plan page can appear in searches for non-technical skills, because the agents have no role filter.

### Results of 2026-11-24

Two changes were chosen on `dev` and confirmed once on `test`, and then the whole set was run again with them. The knowledge base was 829 interview chunks and 127 learning chunks. The model was `openai/gpt-oss-120b`, one run per case.

| | Cutoff 0.30, freeCodeCamp 0.8 (before) | Cutoff 0.40, freeCodeCamp 0.7 (after) |
|---|---|---|
| Retrieval precision | 0.62 | 0.77 |
| Retrieval hit rate / MRR | 0.97 / 0.95 | 0.97 / 0.95 |
| Retrieval "expect nothing" cases correct | 5 of 6 | 6 of 6 |
| Learning plan pass rate | 22 of 26 (0.85) | 24 of 26 (0.92) |
| Ranking cases | 13 of 16 | 15 of 16 |
| Fairness cases (the Docker pair) | 0 of 2 | 2 of 2 |

The files are `retrieval_v1_20261124T112652Z.json` and `roadmap_v1_20261124T112658Z.json` in `data/eval/results/`. The "before" runs are `retrieval_v1_20261124T110732Z.json` and `roadmap_v1_20261124T110737Z.json`.

**Cutoff 0.30 to 0.40.** On `dev`, precision went from 0.61 to 0.81, with hit rate and MRR unchanged. On `test` it went from 0.62 to 0.66 (9 cases, so a small gain), with hit rate and MRR still 1.00. Every "expect nothing" case now comes back empty, and no case that passed before started failing. Changed in `app/rag/retriever.py`.

**freeCodeCamp trust 0.8 to 0.7.** A freeCodeCamp topic page (`news/tag/docker`) ranked above the official Docker page. A higher trust weight (0.4 or 0.5) fixed it, and so did a lower freeCodeCamp trust (0.7 or 0.6). The lower trust was chosen because it is the narrower change: it touches one source and not the weights of every ranking. It also puts freeCodeCamp level with the handbook. On `dev` all 12 ranking cases passed, including the counter-cases where a community roadmap should win. On `test` the ranking cases gave the same result as before (3 of 4), so nothing got worse. The test split has no Docker case, so the Docker fix itself rests on `dev`. Changed in `app/agents/candidate/roadmap_agent.py`.

**Cases that still fail (known weaknesses, not changed).**

- `ret-034`, the query `k8s`: no Kubernetes page is found, because the embedding model does not link the abbreviation to the word. The label stays. An alias list (k8s to Kubernetes) would fix it. It is a separate piece of work.
- `rdm-008`, Cold calling and Sales negotiation for a Sales Representative: the plan linked `pytorch.org/tutorials` and `roadmap.sh/devrel` and carried no notice, so a sales candidate would be shown unrelated tutorials. This is a real weakness, not a wrong label. The agent searches `learning resources for <skill>`, and the words "learning resources" are themselves close to generic tutorial pages, so they pass the cutoff for any skill. The plain queries in the retrieval set do not have that wording, which is why `ret-032` now passes and this case does not. The likely fix is a shorter query (the skill alone, or the skill and one word). It was not tried, because every try needs a full run of model calls and the baseline should show what the system does today. To be done as its own change, with `dev` runs and one `test` confirmation.
- `rdm-017`, TypeScript for a Frontend Developer: `roadmap.sh/typescript` ranked above `typescriptlang.org/docs`, and both are in the plan. This case is in `test`, so it was not tuned for. The label may be too strict, because roadmap.sh is a reasonable first page for this role. A person decides this when the labels are checked. Nothing is changed to make it pass.

### What it does not do

- It does not judge how good the plan's text is. It checks properties: links, sources, order, canary words.
- It is small. A slice of two to five cases is a signal to look at, not a statistic.
- There are no cases for the interview agent's questions yet. The retrieval cases cover its collection.
- It does not run in CI. It needs ChromaDB and a Groq key, so it runs on demand.
- The old Phase 2 set (`data/eval/retrieval_eval.json`, 20 keyword cases run by `backend/scripts/eval_retrieval.py`) targeted the old collections. It was removed in task 15. It stays in git history, and the retrieval set above replaces it.

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
| 13 | **Built, first results recorded 2026-11-24.** Two golden sets (63 cases), runners with comparison and settings sweeps, an optional LangSmith upload, and 345 unit tests (Decision 10). The cutoff (0.40) and freeCodeCamp's trust (0.7) were tuned from the first runs. The cases are drafts to be checked, and v1 is not frozen yet |
| 14 | **Built, run against the real ChromaDB.** `tests/integration/test_knowledge_layer.py` covers ingest, idempotency, changes, the licence gate, link-only pages, the expiry sweep, every retriever filter, ranking, the cutoff, citations and every fallback route, on throw-away collections that are dropped afterwards. It also reads the real collections without writing to them. `test_candidate_flow.py` runs both agents with real retrieval and Groq and checks that every link and source is a stored page and that an answer without sources carries the notice. Run with `-m integration` |
| 15 | **Built.** The five old collections were deleted from ChromaDB (2026-11-24, after a verified snapshot), and the old `retrieve()`, `retrieve_across_collections()`, `add_documents()`, `get_vector_store()`, `seed_data.py`, `eval_retrieval.py` and the Phase 2 evaluation file were removed. `COLLECTIONS` now lists only the two knowledge collections |

---

## Findings from reading the code

- `RetrieverAgent` (`agents/shared/retriever_agent.py`) was used nowhere and could not have worked: it `await`ed a function that is not async and passed `collection=` where the retriever expects `collection_name=`. Removed in task 9.
- The real ingest runs on 2026-11-19 worked with ChromaDB's `upsert`, `get` with `where`, and `update` calls, and ChromaDB accepted the expiry filter (`$lte` on `expires_at_ts`, inside `$and` for one source). That filter matched nothing so far, and `delete` has not run for real. The retriever's filters (`$in`, `$gt`, `$and`) and the `embeddings` option were checked against an in-memory ChromaDB 0.5.23 (the version this project pins), which also ran the expiry sweep end to end. The unit tests use a fake store. Task 14 (`tests/integration/`) runs all of this against the ChromaDB container with the real embedding model, and it passed on 2026-11-24.
- The old roadmap agent called the synchronous `retrieve()` straight from an async method, which blocked the event loop for the length of every search. Fixed in task 11 (Decision 8).
- The roadmap agent stopped searching once it had 12 chunks, so with five or more skill gaps the later skills got no search. Fixed in task 12 (Decision 9).
- The first link check treated a `HEAD` 404 as proof that a page was gone. The first real run showed a site that answers `HEAD` with 404 for a page that loads. Fixed in task 12.

## Open items

- Default `expires_at`: the adapters use 90 days for a static repository (`DEFAULT_EXPIRY`). The ingest command has no option to change it yet. Add one per source if a source needs it.
- **Share-alike licences** (CC-BY-SA) are refused for full text. Someone must decide whether we can accept them and what that means for our own content. To allow them, move their names from `NEEDS_DECISION` to `ALLOWED_FULL_TEXT` in `licence.py`.
- The GitHub MCP server was confirmed on a real run on 2026-11-19 (image tag, `stdio` subcommand, response shape, folder listing). Stdio is refused in production, so ingest runs from a development or CI machine until the server is hosted over HTTP. See `docs/architecture/mcp.md`, section 10.
- The `domain` list is a starting set. Phase 9 will extend it for non-technical roles.
- The retrieval defaults for variety (0.7) and chunks per page (2) are still starting values. The cutoff was changed to 0.40 in task 13 (Decision 10).
- **Live help for gaps (not planned).** Two ideas stay possible if the golden set shows the general-knowledge answers are too weak: a short list of trusted sites fetched live (our own topic-to-page table, nothing stored), or a test of Groq's `browser_search` for its cost, output format and storage rules. Each needs vetting first, and neither is scheduled.
- The wording of the two fallback notes is a first version. Revisit it in task 16, when the screen shows the notice.
- **No role filter in the agents (known limit).** See Decision 8. Non-technical roles may get weak software chunks until Phase 9 adds a role-to-family mapping. Task 13 measures it.
- **The interview prompt still says "Reference material from real interviews".** The knowledge base holds preparation guides, not real interviews, so the wording is a little off. Fix it when the prompt is next touched.
- **The frontend still reads the old resource `source` key.** `AnalysisView.tsx` uses it only as a tooltip. Task 16 updates it and shows `sources` and `notice`.
- **The context budgets are starting values.** 12 resources and 500 characters per chunk for the roadmap; 3000 characters and 700 per chunk for the interview. Task 13 can tune them.
- **The link check is manual.** Run `python -m app.ingest.run --source <name> --dry-run` after any list change. A scheduled run belongs with CI.
- **The `github` token is not set on the development machine.** The handbook cannot be re-ingested until `GITHUB_PERSONAL_ACCESS_TOKEN` is in `backend/.env`. It is to be set before the last task of Phase 7. Until then, run the link sources one at a time, not with `--source all`.
- **Links inside the handbook's text are not checked.** Only the link-only lists get the check.
- **freeCodeCamp's terms, its YouTube channel's terms and the licence of its news articles are unread.** This has no effect while we only link (vetting log).
- **The three ranking weights (0.6, 0.3, 0.1) are still starting values.** freeCodeCamp's trust was lowered to 0.7 in task 13 (Decision 10). Non-technical roles are thin in the official-documentation list until Phase 9.
- **The evaluation set is measured but not frozen.** The first scores are recorded (Decision 10, 2026-11-24), but all 63 labels are still `model-draft`. A person must check them (`--require-reviewed`), then `v1` is frozen and the two result files are copied to `retrieval.v1.baseline.json` and `links.v1.baseline.json` beside the data. Then a tolerance is agreed.
- **Short names are not understood.** The query `k8s` finds no Kubernetes page (`ret-034`). A small alias list (k8s to Kubernetes, and similar) could fix it. Not scheduled.
- **A skill with no matching content can still get unrelated links.** `rdm-008` (sales skills) was shown PyTorch tutorials, because the roadmap agent's query starts with "learning resources for", which is close to any tutorial page. A shorter query is the likely fix. Not scheduled. Decide before Phase 9 adds non-technical sources, which may hide it.
- **`rdm-017` (TypeScript) fails its ranking check.** roadmap.sh ranks above typescriptlang.org. The label may be too strict. A person decides it when the labels are checked.
- **The handbook's study-plan page can appear for non-technical skills.** It is tagged for technical roles, and the agents do not filter on role family. This is the same gap as the missing role filter above.
- **The LangSmith upload has not run for real.** It is tested with a fake client only. Run `python -m evals.langsmith_upload --dry-run` first, and upload only if you want the cases in LangSmith.

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
| 2026-11-23 | Added Decision 10: the golden evaluation set (task 13). Two sets (37 retrieval and 26 learning-plan cases), runners with saved runs and comparison, settings sweeps, an optional LangSmith upload, and unit tests. Recorded the real ingest of the two new link lists (`learning_knowledge` now 127 chunks) and the first `dev` runs. Updated the status, the task table and the open items |
| 2026-11-24 | Recorded the first runs of the evaluation set against the real system (Decision 10): retrieval precision 0.62 to 0.77 and learning-plan pass rate 0.85 to 0.92. Changed the retrieval cutoff from 0.30 to 0.40 and freeCodeCamp's trust from 0.8 to 0.7. Listed three failing cases as known weaknesses. `v1` is not frozen because the labels are unchecked, and no tolerance is agreed |
| 2026-11-24 | Marked task 14 built: integration tests run the knowledge layer and both agents against the real ChromaDB. Removed the open item and the finding that said they had not |
| 2026-11-24 | Task 15: deleted the five old collections from ChromaDB after checking a snapshot of them, and removed the old retrieval code (`retrieve()`, `retrieve_across_collections()`, `add_documents()`, `get_vector_store()`), the seed and Phase 2 evaluation scripts, and the Phase 2 evaluation file. `app/rag/collections.py` lists only the two knowledge collections. Updated the status, the task table and the old-data notes |