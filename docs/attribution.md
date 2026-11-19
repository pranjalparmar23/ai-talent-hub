# Attribution and third-party content

Credits and licence notices for outside material used by the AI Talent Hub knowledge layer (Phase 7).

**Tracker:** Phase 7, tasks 6, 7, 10 and 12. **Last updated:** 2026-11-19.
**Related:** `docs/mcp/vetting-log.md` (why each source got its verdict), `docs/architecture/knowledge-layer.md` (how credit is stored).

This is a record of what we use and how we credit it, not legal advice.

---

## How credit works in the app

- Every stored chunk carries its source id, page URL, licence and credit line as metadata. Retrieval returns them as the citation, and the interface shows them next to the answer (tasks 9 and 16).
- The licence gate (`app/ingest/licence.py`) refuses any document without a recorded licence and credit line. Share-alike licences (CC-BY-SA) are refused until a human decides how they affect our corpus.
- When nothing in the knowledge base matches, no source is shown. The answer comes from the model's own knowledge and is labelled as not based on our reviewed sources (task 10).
- There are two ways to use a source:
  - **Full text:** only when the licence allows copying. The credit line and licence notice stay with the content.
  - **Link only:** for restrictive or unconfirmed licences. We store a link, a title and a short description that we wrote ourselves. Nothing is copied from the source.
- Link-only lists are checked for dead pages on every ingest (task 12). The check sends only a page's address to that page's own site, with no cookies and no user data. A page the site says is gone is left out, and any copy stored earlier is removed.

---

## Full-text sources

### Tech Interview Handbook

| | |
|---|---|
| Source | https://github.com/yangshun/tech-interview-handbook |
| Website | https://www.techinterviewhandbook.org |
| Licence | MIT |
| Credit line stored with every chunk | Tech Interview Handbook, Copyright (c) 2017-Present Yangshun Tay, MIT licence |
| What we use | The guide pages in `apps/website/contents`, including the `algorithms` folder |
| How we read it | The GitHub MCP server (read-only) through the MCP gateway |
| Licence check | The adapter reads the repository's `LICENSE` file on every run. If it is no longer the MIT licence, the run stops and nothing is stored. |
| Re-review by | 2027-02-09 |

**Changes we make to the content.** The pages are cleaned before storing: page headers, imports, components, HTML and images are removed, and links keep their text but lose the address. Paragraphs, list items and table rows that carry affiliate links or sales wording are removed. Pages are split into short chunks for search. We do not add to the text.

**Pages we skip:** `resume-old.md` (an old copy), `best-coding-interview-courses.md` and `mock-interviews.md` (they promote paid third-party products), and template files.

**Licence notice.** The following notice is reproduced from the repository's `LICENSE` file (copy at commit `e1d28e8`, 2026-08-07), as the MIT licence requires:

> MIT License
>
> Copyright (c) 2017-Present Yangshun Tay
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

AI Talent Hub is not affiliated with or endorsed by the Tech Interview Handbook or its authors.

---

## Link-only sources

### roadmap.sh

| | |
|---|---|
| Source | https://roadmap.sh (repository: `kamranahmedse/developer-roadmap`) |
| Licence | Custom licence file. It protects the content by copyright and allows only links to the repository or website. |
| Credit line stored | roadmap.sh by Kamran Ahmed. Link only: no content is copied. |
| What we store | A link, a title and a one-sentence description written by us, for each of the roadmaps listed in `backend/app/ingest/sources/data/roadmap_sh.yaml` |
| What we do not store | Topic lists, text, images or any other content from the roadmaps |
| Re-review by | 2027-02-09 |

Learners who follow a roadmap link go to roadmap.sh itself. The descriptions are ours and are not copied or adapted from the roadmaps. All 41 links answered the link check on 2026-11-19.

### freeCodeCamp

| | |
|---|---|
| Source | https://www.freecodecamp.org, and its YouTube channel https://www.youtube.com/@freecodecamp |
| Licence | **Not confirmed.** A community forum post says the software is BSD-3-Clause and the curriculum is CC-BY-SA-4.0 (the vetting log notes it is not an official statement). The site's own terms, the YouTube channel's terms and the licence of the news articles have not been read. |
| Credit line stored | freeCodeCamp. Link only: no content is copied. |
| What we store | A link, a title and a one-sentence description written by us, for each of the 23 pages in `backend/app/ingest/sources/data/freecodecamp.yaml`: 11 curriculum course pages, 2 YouTube links (the channel and its playlists page) and 10 topic pages of the news site |
| What we do not store | Course text, exercises, articles, videos, transcripts or thumbnails. Single articles and single videos are not listed. |
| Re-review by | 2027-02-09 (the YouTube links: 2027-05-09) |

Because the licence is unconfirmed, freeCodeCamp stays link-only. If a share-alike licence turns out to apply, it does not affect us while we copy nothing.

### Official documentation

| | |
|---|---|
| Source | The front pages of documentation and guides published by the makers of each tool, language or platform. The 44 addresses are in `backend/app/ingest/sources/data/official_docs.yaml`, and each entry names its page. |
| Licence | Every page belongs to its publisher, and the publisher's own licence applies. We copy nothing, so we did not review them one by one. Recorded as `proprietary` (link only). |
| Credit line stored | Official documentation, owned by each publisher. Link only: no content is copied. |
| What we store | A link, a title and a one-sentence description written by us, for each of the 44 pages |
| What we do not store | Any text, code samples, images or other content from the pages |
| Re-review by | 2027-02-09 |

The names of tools, products and companies in the list belong to their owners. AI Talent Hub is not affiliated with or endorsed by any of them. Learners who follow a link go to the publisher's own site. All 44 links answered the link check on 2026-11-19.

### system-design-primer

Approved for link-only use, and **nothing from it is stored yet**. Its licence is not confirmed (the vetting log notes a possible CC-BY-SA share-alike licence). It stays link-only until the licence file is read and the effect on our corpus is decided.

---

## No live web lookups

The app does not search the web or fetch pages live. Brave Search and Tavily were considered for that and are **not adopted** (see `docs/mcp/vetting-log.md`, 2026-11-19). When our knowledge base has nothing for a request, the answer comes from the model's own knowledge. It carries no sources, no links and no quotes, and it is labelled as not based on our reviewed sources (`docs/architecture/knowledge-layer.md`, Decision 7).

Nothing outside the sources listed on this page is ever shown as a cited source. If live lookups are added later, each service needs its own review and an entry here first. A live result must be shown with a link to its source and must not be stored.

---

## Adding a source

1. Vet it (`docs/mcp/vetting-checklist.md`) and record the verdict in `docs/mcp/vetting-log.md`.
2. Read the licence file itself, not a summary of it.
3. Add an adapter (full text) or a link list (link only) under `backend/app/ingest/sources/`. A link list is registered with `link_only=True`, so the link check runs on it, and it gets an entry in `SOURCE_TRUST` in `roadmap_agent.py`, which sets how much it counts when resources are ranked (a test checks both).
4. Add an entry to this file with the credit line, what is stored and what changes we make. Include the licence notice if the licence requires it.
5. Add tests, then run a first ingest and check the stored credit line.