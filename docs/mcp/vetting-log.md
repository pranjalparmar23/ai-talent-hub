# Vetting log

Verdicts for external MCP servers and content sources, following `docs/mcp/vetting-checklist.md`.

- **Date of review:** 2026-11-09. All pages were read on that date; links are under each record.
- **Status:** these are **proposed** verdicts. Nothing is enabled. Approval means, at most, a disabled entry in `backend/app/mcp/mcp_servers.yaml`.
- **Update 2026-11-19:** the live web search plan (Phase 7, task 10) was dropped. Brave Search and Tavily are now marked **not adopted**. Their records stay below as research, in case the idea comes back. When retrieval finds nothing, the app answers from the model's own knowledge instead (`docs/architecture/knowledge-layer.md`, Decision 7).
- **Update 2026-11-19 (Phase 7, task 12):** two link-only lists were added: `official-docs` (front pages of documentation published by the makers of each tool) and `freecodecamp` (courses, the YouTube channel and news topic pages). A link-health check now runs on every link-only ingest. The real runs on 2026-11-19 ended with all 108 links answering (41 roadmap.sh, 44 official docs, 23 freeCodeCamp). The first run had found one moved address and one false "broken", and both were fixed (see the records below).
- **Tracker:** Phase 6, task 9. Rows refer to the "🔌 Tools & Sources" sheet.
- **Evidence quality:** *primary* means the project, registry or vendor page itself. *Index* means a third-party aggregator, so confirm at the source before relying on it. *Unverified* means the page could not be read, and nothing was assumed.
- This is a due-diligence record, not legal advice.

## Summary

| Row | Source | Phase | Proposed verdict |
|---|---|---|---|
| 4 | Official MCP Python SDK | 6 | Approve as a dependency; review the v2 jump before upgrading |
| 6 | MCP Fetch reference server | 7 | **approve-live-only** |
| 6 | Firecrawl MCP | 7 | **reject** (for now; redundant with Fetch) |
| 7 | Brave Search MCP | 7 | **not adopted** (2026-11-19: no live web search, and no free plan any more; was approve-live-only) |
| 7 | Tavily MCP | 7 | **not adopted** (2026-11-19: no live web search; was approve-live-only as a backup) |
| 8 | GitHub MCP server | 7 | **approve-live-only** (read-only mode). Also reads repository files for ingestion from licence-checked repos (disabled entry added 2026-11-18) |
| 9 | Context7 MCP | 7 | **reject** (for now; low fit, limits not found) |
| 10 | Wikipedia MCP (community) | 7 | **reject** the server; Wikipedia content stays **link-only** until its licence is read |
| 11 | LeetCode MCP servers | 7 | **reject** |
| 12 | YouTube transcript MCP | 7 | **reject** |
| 13 | "Interview Prep MCP" (lightcube-labs) | 7 | **reject** (no source found) |
| 14 | Tech Interview Handbook | 7 | **approve-ingest** (MIT; LICENSE file read 2026-11-18, and the adapter re-checks it on every run) |
| 14 | system-design-primer | 7 | **approve-link-only** |
| 14 | roadmap.sh developer-roadmap | 7 | **approve-link-only** (licence file read 2026-11-18: links only) |
| 14 | freeCodeCamp | 7 | **approve-link-only** (23 links added 2026-11-19: course pages and news topics) |
| 14 | freeCodeCamp YouTube channel | 7 | **approve-link-only** (channel and playlist pages only; added 2026-11-19) |
| 14 | Official documentation front pages (curated list) | 7 | **approve-link-only** (44 links; added 2026-11-19) |
| 15 | O*NET Web Services | 9 | **approve-live-only**, with conditions (permission needed) |
| 16 | ESCO web service API | 9 | **reject** (for now; no data licence found) |
| 17 | Third-party O*NET/ESCO wrappers | 9 | **reject** (use the official APIs) |

Not covered here: rows 1–3 and 5 (decided or not MCP) and rows 18–25 (later phases). They get their own review when their phase starts.

## Follow-ups that came out of this review

1. **The official `mcp` package is now at 2.3.0** (released 2026-10-02). We pin `mcp==1.12.4`. Do not upgrade casually: read the v2 migration notes first, then decide in a separate change. The gateway tests will tell us what breaks.
2. **Brave does not allow retaining results on its search plans.** *Update 2026-11-19: no longer needed, because Brave is not adopted.* If a future source forbids keeping its results, the registry would need a `cache: false` option so the gateway never stores them. That option was built on 2026-11-19 and removed the same day because nothing used it. The gateway cache keeps results in memory, and `cache_ttl_seconds: 0` plus `stale_ttl_seconds: 0` only makes an entry expire at once, since the gateway still writes it and removes it on the next lookup.
3. **O*NET terms say registration-required apps need the Center's permission** unless a free, public version exists. Talent Hub has logins. Ask for permission, or confirm that a public view covers it, before enabling.
4. **Production needs HTTP.** The gateway refuses `stdio` in production. Fetch and GitHub are shown above with stdio-style packages. Whether each ships a streamable-HTTP mode was not verified. Plan to run them as self-hosted containers (`internal: true`) or use a hosted endpoint.
5. **Things this session could not read** (marked *unverified* below): LeetCode terms, Wikimedia reuse terms, GitHub API rate limits, Firecrawl robots.txt behaviour, Context7 request limits, the ESCO data licence, the LICENSE files of the content repos, and the terms of the freeCodeCamp site, its YouTube channel and its news articles. The Tech Interview Handbook and roadmap.sh licence files were read on 2026-11-18 (see their records); the others are still unread.
6. **Link health is checked by hand for now.** `python -m app.ingest.run --source <name> --dry-run` asks each site about every link in a link-only list and reports the broken ones without changing anything. It sends `HEAD`, and confirms a 404 or 410 with a one-byte `GET`, because some sites (support.google.com was the first) answer `HEAD` with 404 for a page that exists. Without `--dry-run` it drops the broken links and removes their stored copies. Only 404 and 410 count as broken. A timeout, a block (403, 429) or a redirect to another site counts as unknown and the link is kept, so a site that blocks bots can hide a dead link. The check cannot see a page that still answers 200 but no longer says what our description says. Running it on a schedule is a later CI task.

---

### Official MCP Python SDK (`mcp`) — 2026-11-09
- Type: library (dependency, not a content source)
- Offers: Python client and server implementation of MCP; our gateway is built on it.
- Links (accessed 2026-11-09): [PyPI](https://pypi.org/project/mcp)
- Rights: MIT licence (primary).
- Maintenance: version 2.3.0 released 2026-10-02; published by "Model Context Protocol, a Series of LF Projects, LLC"; four PyPI maintainers; Python 3.10+.
- Tools: n/a
- Data sent / auth: n/a
- Limits and cost: free.
- Quality and coverage: n/a
- Risks found: our pin (1.12.4) is far behind the current major version. Breaking changes in v2 not reviewed.
- Verdict: approve as a dependency
- Conditions: keep the exact pin; read the v2 migration notes before any upgrade; upgrade in its own change with the full gateway test suite.
- Re-review by: 2027-02-09

### MCP Fetch reference server — 2026-11-09
- Type: MCP server
- Offers: fetch a web page and return it as Markdown.
- Links (accessed 2026-11-09): [PyPI mcp-server-fetch](https://pypi.org/project/mcp-server-fetch/)
- Rights: MIT (primary). Fetched pages remain the property of their owners, so see Risks.
- Maintenance: version 2026.11.4 released 2026-11-04; author Anthropic, PBC; five PyPI maintainers listed.
- Tools: `fetch` → read-only (HTTP GET of an arbitrary URL). Parameters: `url`, `max_length`, `start_index`, `raw`, `timeout_ms`. Also one prompt, `fetch`.
- Data sent / auth: only the URL. No key.
- Limits and cost: free; no rate limit of its own. Our gateway rate limit applies.
- Quality and coverage: depends on the page fetched.
- Risks found: arbitrary-URL tool, so SSRF and copyright. By default it obeys robots.txt for model-initiated fetches and refuses private, loopback and link-local addresses, but its own page says that guard does not protect against DNS rebinding and warns about local/internal access. The flags `--ignore-robots-txt` and `--allow-private-ips` remove those protections.
- Verdict: approve-live-only
- Conditions: `allowed_tools: [fetch]`; short `allowed_url_domains` list; never pass `--ignore-robots-txt` or `--allow-private-ips`; pinned version; results are not stored in RAG; gateway SSRF guard stays on; HTTP deployment per follow-up 4.
- Re-review by: 2027-05-09

### Firecrawl MCP — 2026-11-09
- Type: MCP server (vendor)
- Offers: scrape, crawl, search and extract web content through the Firecrawl cloud or a self-hosted instance.
- Links (accessed 2026-11-09): [npm firecrawl-mcp](https://npmjs.com/package/firecrawl-mcp)
- Rights: MIT for the server (primary). Terms of the Firecrawl service and of crawled sites not reviewed (unverified).
- Maintenance: npm version 1.11.0, no publish date on the page. Publisher not named on the page (npm collaborator `hello_sideguide`; install instructions use the `@mendableai` namespace).
- Tools: `scrape`, `batch_scrape`, `map`, `search`, `crawl` → fetch many pages; `check_batch_status`, `check_crawl_status` → status; `extract`, `deep_research` → LLM-assisted; `generate_llmstxt` → returns text. None writes to a target site, but several can fetch many URLs.
- Data sent / auth: URLs and queries go to a third-party cloud. `FIRECRAWL_API_KEY` (optional if self-hosted via `FIRECRAWL_API_URL`).
- Limits and cost: credit-based; the page warns large crawls hit rate limits. Plan limits not read (unverified).
- Quality and coverage: n/a
- Risks found: bulk-crawl tools burn credits and fetch many third-party pages; robots.txt handling unverified; unclear publisher on the page; Fetch already covers our need.
- Verdict: reject (for now)
- Conditions: revisit only if Fetch proves inadequate; then require a read of Firecrawl's terms and robots handling, and allow `scrape` only.
- Re-review by: 2027-05-09

### Brave Search MCP — 2026-11-09 (updated 2026-11-19)
- Type: MCP server (vendor)
- Offers: web, news, image, video and local search.
- Links (accessed 2026-11-09): [npm @brave/brave-search-mcp-server](https://www.npmjs.com/package/@brave/brave-search-mcp-server); [Brave API help](https://api-docs.search.brave.software/resources/help)
- Rights: MIT for the server (primary). API output: the Brave help page says the "Data for Search" and "Data for AI" plans do **not** permit retaining any part of the output; only the "Data with storage rights" plan does. The API grants no rights to third-party content.
- Maintenance: version 1.3.4 when read on 2026-11-09 (no publish date on the page); published under the `@brave` scope, repo under Brave's organisation. *Update 2026-11-19:* npm now shows version 2.1.5 (modified 2026-11-17), and 2.0.85 exists, so the 2.x line is current. The 1.3.4 review no longer matches what would be installed.
- Tools: `brave_web_search`, `brave_local_search`, `brave_video_search`, `brave_image_search`, `brave_news_search`, `brave_summarizer` → all read-only.
- Data sent / auth: search terms only. `BRAVE_API_KEY`. HTTP transport by default.
- Limits and cost: the npm page listed a free plan with 2,000 queries a month, and Brave's help page said the free plan needed a credit card (anti-fraud, "never billed"). *Update 2026-11-19:* **that is out of date.** The plans page in the project owner's Brave dashboard no longer offers a free plan. The Search plan is prepaid at $5.00 per 1,000 requests, and the Answers plan is $4.00 per 1,000 queries plus tokens.
- Quality and coverage: general web coverage, including non-technical topics.
- Risks found: retention rule (follow-up 2); links lead to pages with their own copyright. *Added 2026-11-19:* the output format of the server is not described in its documentation, and it was never run, so nothing about what it returns was confirmed.
- Verdict: **not adopted (2026-11-19).** It was approve-live-only on 2026-11-09.
- Decision (2026-11-19): the project owner decided not to pay for a search service or to rely on an outside one. A short-lived registry entry and a live-lookup module were built and then removed, so nothing from this record is in the code or enabled. The fallback when retrieval finds nothing is the model's own knowledge, clearly labelled (`docs/architecture/knowledge-layer.md`, Decision 7).
- Conditions, kept only in case this is reopened: `allowed_tools: [brave_web_search]` (add `brave_news_search` later if needed); a paid plan whose terms were read again; **no storing results** (no ingest into RAG, and the gateway must not cache them); show the source link to the user; send short public search phrases only; `rate_limit_per_minute` well under the plan cap; a daily cap in code; key in `.env` as `BRAVE_API_KEY`; the server's output format checked against a real response; read the 2.x notes before pinning a version.
- Re-review by: 2027-02-09, only if live search is reconsidered

### Tavily MCP — 2026-11-09 (updated 2026-11-19)
- Type: MCP server (vendor)
- Offers: web search, page extraction, site crawl and mapping, and multi-source research.
- Links (accessed 2026-11-09): [Docker MCP listing](https://hub.docker.com/mcp/server/tavily/overview); [Terms](https://www.tavily.com/terms); [API credits](https://docs.tavily.com/documentation/api-credits.md)
- Rights: MIT for the server (primary). Terms (primary): §6.5 lets Tavily and its AI providers use Customer Input and Outputs for training and improving AI models; §7 allows sharing input with affiliates and vendors; §2 grants use "solely for internal business purposes"; §8.2 requires that input contain no sensitive data such as passwords or health information; caching or storing output is not addressed.
- Maintenance: upstream author is tavily-ai; the Docker page is a third-party packaging (publisher field blank). No version or date seen (unverified). GitHub activity not checked.
- Tools: `tavily_search`, `tavily_extract`, `tavily_crawl`, `tavily_map`, `tavily_research` → all fetch or search; `extract`, `crawl` and `map` take URLs; `research` is rate limited to 20 requests a minute.
- Data sent / auth: search terms only. `TAVILY_API_KEY`.
- Limits and cost: free plan 1,000 credits a month; search costs 1 credit (basic) or 2 (advanced). Not rechecked on 2026-11-19.
- Quality and coverage: general web.
- Risks found: our queries may be used to train their models, so send **only public search terms**; "internal business purposes" may not fit a user-facing product (read §2 before enabling); URL-taking tools widen SSRF exposure.
- Verdict: **not adopted (2026-11-19).** It was approve-live-only, as a backup to Brave.
- Decision (2026-11-19): the live web search plan was dropped (see the Brave record), so the backup is not needed. Nothing is enabled or built for it.
- Conditions, kept only in case this is reopened: `allowed_tools: [tavily_search]` only; no `extract`, `crawl`, `map` or `research`; search terms only, never resume or JD text; confirm §2 fits our use before `enabled: true`.
- Re-review by: 2027-02-09, only if live search is reconsidered

### GitHub MCP server — 2026-11-09
- Type: MCP server (vendor, official)
- Offers: repository, issue and pull-request access through GitHub's API.
- Links (accessed 2026-11-09): [pkg.go.dev](https://pkg.go.dev/github.com/github/github-mcp-server)
- Rights: MIT for the server (primary). The licence of each repo we read is separate and must be checked per repo.
- Maintenance: pkg.go.dev shows v1.14.0 (2026-10-02) and says a higher major version (v2) exists.
- Tools: configurable toolsets (defaults: context, repos, issues, pull_requests, users). Write tools exist; the page states write tools are skipped when `--read-only` is set.
- Data sent / auth: repo names and paths; a GitHub access token (a fine-grained, read-only token for public repos is enough).
- Limits and cost: GitHub API rate limits not checked this session (unverified).
- Quality and coverage: whatever repos we choose.
- Risks found: the default toolsets include write-capable tools; issue and pull-request text from strangers is untrusted data (prompt injection).
- Verdict: approve-live-only
- Conditions: run with `--read-only` and `--toolsets repos` only; `allowed_tools` limited to file and repo read tools; read-only fine-grained token in `.env`; ingest only from repos whose licence has been checked; v2 differences reviewed before pinning.
- Status (2026-11-19): a **disabled** `github` entry is in `mcp_servers.yaml` (Docker image `ghcr.io/github/github-mcp-server:v1.14.0`, `--read-only --toolsets repos`, `allowed_tools: [get_file_contents]`, token from `GITHUB_PERSONAL_ACCESS_TOKEN`). The Tech Interview Handbook adapter reads files through it. **Confirmed on 2026-11-19:** a real ingest with this image and tag read all 45 Handbook pages through it. The file text arrives in an embedded resource, which the gateway keeps, and the folder listings were read correctly. Stdio is refused in production, so ingest runs from a development or CI machine until an HTTP deployment exists.
- Re-review by: 2027-02-09

### Context7 MCP — 2026-11-09
- Type: MCP server (vendor)
- Offers: up-to-date library documentation for coding assistants.
- Links (accessed 2026-11-09): [Snyk page for the npm package](https://security.snyk.io/package/npm/%40upstash%2Fcontext7-mcp); [plans page](https://context7.com/docs/docs7/plans.md)
- Rights: MIT; Snyk lists 0 direct vulnerabilities (index). Terms not read (unverified).
- Maintenance: `@upstash/context7-mcp` 4.5.1 published 2026-11-06; frequent releases.
- Tools: not read this session (unverified).
- Data sent / auth: library names and questions to Upstash; key requirement not found.
- Limits and cost: the plans page I reached describes documentation-site plans, not API request limits. Request limits unverified.
- Quality and coverage: aimed at library documentation, not interview preparation.
- Risks found: low fit for the product; terms and limits unknown.
- Verdict: reject (for now)
- Conditions: revisit if a feature needs framework documentation.
- Re-review by: 2027-05-09

### Wikipedia MCP (community server by Ravi Kumar E.) — 2026-11-09
- Type: both (MCP server, and Wikipedia content)
- Offers: search and read Wikipedia articles.
- Links (accessed 2026-11-09): [PyPI wikipedia-mcp 1.7.0](https://pypi.org/project/wikipedia-mcp/1.7.0/)
- Rights: server is MIT (primary). **Wikipedia content licence and reuse terms: unverified** (the Wikimedia page could not be fetched).
- Maintenance: single maintainer account; releases listed up to 2.0.1 (2026-05-01). The 1.7.0 files carry no provenance attestation and were not published with Trusted Publishing.
- Tools: `search_wikipedia`, `get_article`, `get_summary`, `get_sections`, `get_links`, `get_coordinates`, `get_related_topics`, `summarize_article_for_query`, `summarize_article_section`, `extract_key_facts`, `test_wikipedia_connectivity` → all read-only.
- Data sent / auth: search terms; optional Wikipedia token. The SSE transport has no built-in endpoint authentication.
- Limits and cost: free; Wikipedia rate limits unverified.
- Quality and coverage: encyclopedic background, not interview-specific.
- Risks found: community package from one maintainer; no attribution handling; unclear which Wikipedia API it uses.
- Verdict: reject (the server)
- Conditions: for background content use **link-only** (title, URL, own-words summary). If we later want Wikipedia text, read the Wikimedia reuse terms (attribution and share-alike) first and call the official Wikipedia API from our own tool.
- Re-review by: 2027-05-09

### LeetCode MCP servers (checked: `@jinzcdev/leetcode-mcp-server`) — 2026-11-09
- Type: MCP server (community)
- Offers: LeetCode problems, user data and solutions through MCP.
- Links (accessed 2026-11-09): [npm](https://npmjs.com/package/@jinzcdev/leetcode-mcp-server). LeetCode's [terms](https://leetcode.com/terms) could not be fetched (unverified).
- Rights: server is MIT (primary). **LeetCode terms: unverified**, so nothing can be assumed about automated access or copying problem text.
- Maintenance: version 1.1.0, no publish date on the page; one publisher account.
- Tools (16): `get_daily_challenge`, `get_problem`, `search_problems`, user tools (`get_user_profile`, `get_user_contest_ranking`, `get_recent_ac_submissions`, `get_recent_submissions`, `get_user_status`, `get_problem_submission_report`, `get_problem_progress`, `get_all_submissions`), note tools including `create_note` and `update_note` → **side effects**, and solution tools (`list_problem_solutions`, `get_problem_solution`).
- Data sent / auth: public problem data needs no login; user data needs a LeetCode **session cookie**.
- Limits and cost: unknown.
- Quality and coverage: coding problems only.
- Risks found: session cookie (credential broader than read-only), write tools, solution text, unverified terms.
- Verdict: reject
- Conditions: if we want LeetCode problems in learning resources, add **hand-written link entries** (title, URL, topic, our own words). Do not copy statements or solutions.
- Re-review by: 2027-05-09

### YouTube transcript MCP (checked: `@kimtaeyoon83/mcp-server-youtube-transcript`) — 2026-11-09
- Type: MCP server (community)
- Offers: fetch the transcript of a YouTube video.
- Links (accessed 2026-11-09): [mcpservers.org page](https://mcpservers.org/en/servers/github-com-kimtaeyoon83-mcp-server-youtube-transcript) (index); [YouTube API Services Developer Policies](https://developers.google.com/youtube/terms/developer-policies) (primary)
- Rights: server is MIT (index). The page does not say how transcripts are obtained. The Developer Policies say not to scrape YouTube, not to use anything but the API to retrieve API data, and not to download or cache copies of audiovisual content without written approval. Applying those rules to captions is my reading; the page does not mention transcripts.
- Maintenance: not checked beyond the index page (unverified).
- Tools: `get_transcript` → read-only.
- Data sent / auth: video URL; no key.
- Limits and cost: unknown.
- Quality and coverage: video captions.
- Risks found: no API key and no stated mechanism suggest unofficial access, which conflicts with the policies above; transcripts are copyrighted by the video owner.
- Verdict: reject
- Conditions: use **link-only** for videos (title, channel, URL, our own summary). If we want metadata at scale, use the official YouTube Data API within its policies (30-day limit on stored API data).
- Re-review by: 2027-05-09

### "Interview Prep MCP" (lightcube-labs) — 2026-11-09
- Type: MCP server (community)
- Offers: unknown.
- Links (accessed 2026-11-09): none found. A search for it returned unrelated interview-prep servers, which were not evaluated.
- Rights: no licence found.
- Maintenance: unverified.
- Tools / Data sent / Limits / Quality: unverified.
- Risks found: no source, no licence, unknown maintainer.
- Verdict: reject (no licence or terms can be found)
- Conditions: reopen only if someone supplies the repository link.
- Re-review by: 2027-05-09

### Tech Interview Handbook (`yangshun/tech-interview-handbook`) — 2026-11-09
- Type: data source (GitHub repository)
- Offers: coding interview preparation guides.
- Links (accessed 2026-11-09): [ecosyste.ms index](https://awesome.ecosyste.ms/projects/github.com%2Fyangshun%2Ftech-interview-handbook) (index)
- Rights: MIT. The LICENSE file was read on 2026-11-18 (copy at commit e1d28e8): "MIT License", "Copyright (c) 2017-Present Yangshun Tay", standard grant. MIT requires keeping the copyright and licence notice (see `docs/attribution.md`). The adapter re-reads the file on every run and stops if it is no longer MIT.
- Maintenance: owner yangshun; about 126k stars. The index shows last push 2025-04-28 but was last synced 2025-05-05, so current activity is unknown.
- Tools: n/a
- Data sent / auth: none (public repo).
- Limits and cost: GitHub rate limits when fetching.
- Quality and coverage: strong on software roles, thin on non-technical roles. The page promotes paid third-party courses, which we must not ingest.
- Risks found: age of content; promotional links; coverage gap.
- Verdict: approve-ingest
- Conditions: read the LICENSE file at ingest time; store source name, URL, licence and access date with every chunk; include the MIT notice in an attribution file; skip promotional content; run the link-health check.
- Status (2026-11-18, updated 2026-11-19): met in code. The LICENSE is checked on every run, the credit line is stored with every chunk, promotional content is removed (on the checked copy: 45 promotional items across 12 pages, plus 3 pages skipped outright and a template file), and the notice is in `docs/attribution.md`. The link-health check (Phase 7, task 12) runs on link-only lists, not on the Handbook. Its pages are read from the repository on every run, so a page removed upstream stops being refreshed, and the expiry sweep removes it once its 90 days are up. Outbound links inside the Handbook's page text are not checked (open).
- Re-review by: 2027-02-09

### system-design-primer (`donnemartin/system-design-primer`) — 2026-11-09
- Type: data source (GitHub repository)
- Offers: system design study material.
- Links (accessed 2026-11-09): [ecosyste.ms index](https://awesome.ecosyste.ms/projects/github.com%2Fdonnemartin%2Fsystem-design-primer) (index)
- Rights: the index lists the licence as "other" with a LICENSE.txt, and says some lists label it CC-BY-SA-4.0. **Not confirmed**: the file was not read.
- Maintenance: last push 2026-09-15 per the index (synced 2026-10-18).
- Tools: n/a
- Data sent / auth: none.
- Limits and cost: n/a
- Quality and coverage: technical roles only.
- Risks found: unconfirmed licence; if it is share-alike, adapted text could carry obligations.
- Verdict: approve-link-only
- Conditions: store title, URL and our own short summary. Upgrade to ingest only after LICENSE.txt is read and the obligations are written down.
- Re-review by: 2027-02-09

### roadmap.sh developer-roadmap (`kamranahmedse/developer-roadmap`) — 2026-11-09
- Type: data source (GitHub repository)
- Offers: role-based learning roadmaps.
- Links (accessed 2026-11-09): [ecosyste.ms index](https://awesome.ecosyste.ms/projects/github.com%2Fkamranahmedse%2Fdeveloper-roadmap) (index)
- Rights: custom licence file, read on 2026-11-18 (primary, copy at commit 16ea08c). It says the text and images are protected by copyright, that personal use is allowed, that **links** to the repository or roadmap.sh may be shared, and that any other use of the content needs prior consent. Copyright "© 2017 - Present. Kamran Ahmed".
- Maintenance: the index shows a last push in 2025-05 but another entry cites 2026-02; current activity unverified.
- Tools: n/a
- Data sent / auth: none.
- Limits and cost: n/a
- Quality and coverage: mostly technical roles, with a few non-technical ones (product, design, writing, SEO).
- Risks found: restrictive custom licence. Our own descriptions must not copy or closely follow the roadmaps' text.
- Verdict: approve-link-only
- Conditions: link and short own-words summary only; never fetch or store the roadmaps' content. Licence file read (see Rights).
- Status (2026-11-18): done. 41 links with descriptions written by us are in `backend/app/ingest/sources/data/roadmap_sh.yaml` (Phase 7, task 6). The addresses match the repository's roadmap folders. The live pages were checked with the link-health check on 2026-11-19, and all 41 answered.
- Re-review by: 2027-02-09

### freeCodeCamp — 2026-11-09
- Type: data source (GitHub repository and curriculum)
- Offers: free coding curriculum.
- Links (accessed 2026-11-09): [freeCodeCamp forum post on licensing](https://forum.freecodecamp.org/t/freecodecamps-licensing-justification/175962)
- Rights: the forum post says software is BSD-3-Clause and curriculum content is CC-BY-SA-4.0. The post is a **community reply, not an official statement**, so confirm in the repo. CC-BY-SA requires attribution and sharing adaptations under the same licence.
- Maintenance: not checked (unverified).
- Tools: n/a
- Data sent / auth: none.
- Limits and cost: n/a
- Quality and coverage: technical learning content.
- Risks found: share-alike on adapted content; unofficial licence source.
- Verdict: approve-link-only
- Conditions: links and our own summaries only until the repo's licence files are read and the share-alike effect on our corpus is decided.
- Status (2026-11-19): done for the links in `backend/app/ingest/sources/data/freecodecamp.yaml` (Phase 7, task 12). There are 23 links, each with one sentence we wrote: 11 course pages under `/learn`, 2 video links (see the next record) and 10 topic pages of the news site. Nothing is fetched or stored from them. Single articles are not listed on purpose, because they move and the check cannot tell that one has changed. The course addresses are the most likely to change, because freeCodeCamp renames its certifications. A link-health check on 2026-11-19 found all 23 answering. Still unread: freeCodeCamp's own terms and the licence of its news articles. That has no effect while we only link.
- Re-review by: 2027-02-09

### freeCodeCamp YouTube channel (links only) — 2026-11-19
- Type: data source (links to a public channel)
- Offers: long free video courses on programming and computer science.
- Links (accessed 2026-11-19): `https://www.youtube.com/@freecodecamp` and `https://www.youtube.com/@freecodecamp/playlists`, both in `freecodecamp.yaml`. The link check got an answer from both on 2026-11-19. YouTube's own terms were **not read** for this review (unverified).
- Rights: the videos belong to the channel and its authors. We link only: no download, no transcript, no embed, no thumbnail. This follows the YouTube transcript record above (reject the server; link-only for video).
- Maintenance: not checked.
- Tools: n/a
- Data sent / auth: the link check sends only the page address. No key.
- Limits and cost: free.
- Quality and coverage: broad, mostly technical. The quality of single videos was not reviewed.
- Risks found: single video addresses are not listed, because they could not be confirmed from here and videos get replaced. YouTube may answer a bot with a consent page, a 403 or a 429. The check treats those as unknown and keeps the link, so a removed channel page could go unnoticed.
- Verdict: approve-link-only
- Conditions: channel and playlist pages only. A single video is added only after someone has watched it and written the one-sentence summary. No transcripts. Do not use the YouTube Data API for this without reading its policies first (see the transcript record for the 30-day limit on stored API data).
- Re-review by: 2027-05-09

### Official documentation front pages (curated list `official-docs`) — 2026-11-19
- Type: data source (our own curated list of links)
- Offers: the front pages of documentation and guides published by the makers of a tool, language or platform, for the skills that appear in learning plans.
- Links (accessed 2026-11-19): the 44 addresses are in `backend/app/ingest/sources/data/official_docs.yaml`. The link check requested each one live on 2026-11-19. That shows the page answers and nothing more.
- Rights: every page belongs to its publisher, and the publisher's own licence applies. Some documentation sites are openly licensed and some are not; they were not reviewed one by one, because nothing is copied. We store a link, a title and one sentence we wrote ourselves, the same as roadmap.sh, recorded as `proprietary` (link only).
- Maintenance: the list is ours, and publishers move pages. The first real run on 2026-11-19 found two cases. `docs.claude.com` now redirects to `platform.claude.com`, so the address was updated. `support.google.com` answered `HEAD` with 404 although the page loads, so the checker now confirms a 404 with a `GET` before dropping a link.
- Tools: n/a
- Data sent / auth: the link check sends only the page address to that page's own site, with a User-Agent that names the project. No cookies and no user data.
- Limits and cost: free. At most 5 requests at a time, one at a time per host with a 0.25 s pause.
- Quality and coverage: strong for software and data skills. Thin for non-technical roles: 4 of the 44 links are for non-technical roles and 2 for all roles. The list includes cloud vendors and Anthropic's own prompt guide, so it is not vendor-neutral. A plan treats each as the official source for that tool and nothing more.
- Risks found: pages move or change, and the check only catches 404 and 410. Official pages are not always beginner-friendly. A site that blocks bots can hide a dead link (see follow-up 6).
- Verdict: approve-link-only
- Conditions: the top page of a guide, never a deep page. Addresses written in tidy form. One sentence of our own for each. Run the link check before each ingest and after any change to the list. A new list needs a `SOURCE_TRUST` entry in the roadmap agent (a test checks it).
- Status (2026-11-19): done, 44 links. Second real run: 44 of 44 answered, after the two fixes above.
- Re-review by: 2027-02-09

### O*NET Web Services (US Dept. of Labor) — 2026-11-09
- Type: data source (hosted API; to be wrapped as a gateway tool)
- Offers: occupations with skills, knowledge, abilities and tasks.
- Links (accessed 2026-11-09): [Terms of Service](https://services.onetcenter.org/terms); [Data License](https://services.onetcenter.org/help/license_data)
- Rights (primary): free to use with attribution. A link to the O*NET Web Services site must be shown in a prominent place near the data; the Data License page provides the credit text and the trademark line. Data must be shown **unaltered**. Registered apps only; the app's URL must be registered. The licence is **non-transferable**: if we offer our own API, our clients may not republish O*NET data they receive. Registration-required (paid or login-gated) apps need the Center's permission unless a free public version also exists. The terms do not mention caching or AI use.
- Maintenance: US Department of Labor programme; best-effort service; terms can change on 30 days' email notice.
- Tools: read-only lookups (we wrap them ourselves).
- Data sent / auth: occupation titles or codes only; free registration (API key, as noted in the tracker).
- Limits and cost: free; throttling or suspension above 5 requests a second or 50,000 a day; best-effort availability.
- Quality and coverage: broad, including non-technical occupations, which suits our goal.
- Risks found: login-gated app needs permission; no clear caching rule; unaltered-data rule limits summarising inside the same field.
- Verdict: approve-live-only
- Conditions: ask the Center for permission, or confirm a free public view; show the attribution on every screen that uses the data; do not store it permanently (registry cache only); never alter values; do not expose raw O*NET data through our own API; `rate_limit_per_minute` far under the limits above; key in `.env`.
- Re-review by: 2027-02-09

### ESCO web service API (European Commission) — 2026-11-09
- Type: data source (hosted API)
- Offers: EU occupation and skill classification.
- Links (accessed 2026-11-09): [Use ESCO services (API)](https://esco.ec.europa.eu/en/use-esco/use-esco-services-api)
- Rights: the page says the **API software** is under EUPL 1.2. It does **not** state the licence for the ESCO data, an attribution text, or whether a key is required. Data licence: unverified.
- Maintenance: European Commission service. Two APIs are described: a web-service API and a downloadable local API.
- Tools: n/a (not an MCP server)
- Data sent / auth: unknown.
- Limits and cost: not stated.
- Quality and coverage: the page does not give data languages; the site interface offers 28 languages (not a statement about the data).
- Risks found: data licence and limits unknown.
- Verdict: reject (for now, evidence missing)
- Conditions: when Phase 9 starts, read the "Download ESCO" and ESCO terms pages, then re-run this review. The downloadable local API is an option if the web service is restricted.
- Re-review by: 2027-02-09

### Third-party O*NET/ESCO wrappers (e.g. "jobspipe") — 2026-11-09
- Type: MCP server (community / commercial)
- Offers: O*NET or ESCO data through someone else's service.
- Links (accessed 2026-11-09): none reviewed.
- Rights: unknown. A wrapper would add its own terms on top of the data owner's, and O*NET's licence is non-transferable.
- Maintenance / Tools / Data sent: not reviewed.
- Risks found: extra party between us and the data; unclear licence chain.
- Verdict: reject
- Conditions: call the official APIs through our own gateway.
- Re-review by: 2027-05-09