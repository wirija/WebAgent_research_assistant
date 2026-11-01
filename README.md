# Company Research — WebAgent

A local workspace with three separate modules powered by **GPT-5 mini**: collect official company disclosures, build evidence-backed company understanding, and research the external environment. All modules share one UI at port 8765.

Use the **Collect documents**, **Understand company**, and **External environment** tabs to switch modules without interrupting their work. In Module 2, choose a saved collection and click **Use output** to fill its source folder, then supply the financial year-end and cutoff before starting. You can also enter a folder manually. Selecting a collection does not start analysis. Synthetic collection demos are excluded from the handoff list.

`workspace_ui/` is a presentation layer that mounts the independent HTTP adapters at `/collection/` and `/understanding/` and starts/stops their lifecycles. It does not run a pipeline or import either module's processing engine. Module 1 remains in `webagent/`; Module 2 remains in `modules/company_understanding/`, with its own CLI, file contract and outputs. A future orchestrator should be a separate package responsible for pipeline order and execution. The combined launch uses the root `.env` and separate output directories; each module's browser-entered API key remains scoped to that module. The standalone Module 2 launch continues to use its own configuration.

This is a working prototype, not a guarantee of exhaustive collection. No company-specific URLs or scrapers are configured. The agent discovers websites and archives at runtime and asks for clarification when names are ambiguous.

Identity resolution uses the full input phrase, official company/business information, addresses, and listing details. A place name can be a location hint or part of the company name (for example, a business called “Singapore Noodles” could be Malaysian). The model weighs that evidence, proceeds with a clearly supported match, and records its interpretation. Clarification requires prior search and source inspection and is reserved for unresolved identity questions; overseas namesakes alone should not trigger it.

## Run locally

Install [uv](https://docs.astral.sh/uv/) if needed, then from this directory:

```sh
uv sync
uv run playwright install chromium
uv run python run.py
```

Open **http://127.0.0.1:8765**. In **Settings**, enter an OpenAI API key. It stays in the current browser page's memory and in the server's active API client, never in browser storage or collection files. Alternatively copy `.env.example` to `.env` and set `OPENAI_API_KEY` there. `.env` is ignored by Git.

Live research uses the OpenAI API and incurs model and web-search charges. A ChatGPT subscription is not an API key. The model defaults to the requested `gpt-5-mini`; set `OPENAI_MODEL` in `.env` to change it explicitly. The application does not silently substitute another model.

**Try a sample run** works without a key. It creates three clearly marked synthetic HTML files and demonstrates progress and folder export. It does not call a model or search the web.

## How collection works

1. The Python controller calls GPT-5 mini through the Responses API with custom function tools.
2. The model requests web searches. A separate GPT-5 mini Responses call uses OpenAI's hosted `web_search` tool; returned source URLs become permitted discovery leads.
3. The agent reads pages through HTTPX and Beautiful Soup. It can inspect sitemaps and the first 12 pages of PDFs. For dynamic archives, it can use Playwright to render pages, select years, click pagination, fill a public search field, or scroll.
4. Browser controls receive IDs tied to a specific observation, so stale IDs cannot be reused. Website content is treated as data rather than instructions. There is no arbitrary model-generated Python/JavaScript execution.
5. Registering an official archive starts a persistent collection queue. Python scans every captured link on the page, including those outside the model's excerpt, queues recognized announcement rows and documents, follows observed year/pagination links, opens each announcement, and queues its downloadable attachments. URL and content hashes prevent repeated downloads and duplicate files. HTML announcement pages are retained alongside attachments; HTML-only announcements are saved too. No financial analysis or importance filtering is performed: another program can consume these files and the catalogue.
6. Every captured link receives a stable ID. For unfamiliar archive layouts the model can supply CSS selectors through `scan_archive`; Python applies them to the entire page and reuses them on linked archive pages. The model can also queue individual IDs with `collect_documents`. Exact URL encoding/query strings are preserved. A document target explicitly embedded in a published tracking URL can be followed directly, with the wrapper kept in source evidence; the target still passes robots and public-address checks. External IR/CDN documents are accepted when reached through the official site.
7. Publication dates come from a separate date cell/time element or publication text outside the linked title, including ordinary list rows. Reporting-period dates inside titles are not used. An attachment can inherit its announcement's verified row date, with a source link ID in the catalogue. Otherwise an exact source quote is required, or the date remains unknown and the file is retained for review. Code applies the date window and hashes files.
8. After a failed download, code refreshes and, if necessary, renders its source page and retries the same observed link. Recovery is limited to three download attempts per URL and eight automatic source refreshes per run (HTTP transport retries may occur within each attempt). Refreshed sources are cached across a batch. Persistent failures remain recorded while later queue items continue. The model can inspect recovery observations, click a browser control, or locate an alternative official source. Different URLs are never silently substituted by fuzzy title matching.
9. Queue entries record pending, processing, completed, excluded, failed or interrupted outcomes, parent announcement relationships, and document IDs. Before finishing, all pending work is processed and discovered dated links are reconciled. An archive cannot be marked checked while its queue contains failures; it must be recovered or explicitly marked blocked. Repeating `finish` cannot skip archives still under exploration. Coverage review is still model-assisted, and unknown layouts or undiscovered sources can leave gaps. Stopping retains completed files and marks unfinished queue items interrupted.

The window uses calendar months and the Asia/Singapore date. A reporting period is not a publication date. Each record includes `date_method` (`publication_row`, `announcement_row`, `model_quote`, or `unknown`) and its supporting text. These dates are not independently verified; ambiguous dates remain for review.

## Output

```text
output/<company>-<date>-<run-id>/
  documents/       Original downloaded documents or HTML announcements
  evidence/        Page observations: text, links, and available metadata
  catalogue.json   Document records, persistent queue, sources, usage, and coverage
  catalogue.csv    A spreadsheet-friendly document catalogue
  summary.txt      Outcome and unresolved items
```

Files with unknown publication dates are saved with an `undated-` prefix. Records outside the requested window remain in the catalogue with `excluded_date` status and are not downloaded. Exact duplicates reference an existing saved file. Failed downloads remain visible with error reasons, source-page links, and attempt history. The GUI shows queue progress separately from file outcomes: announcement/document entries, attachments, archive pages, processed items, remaining work, and failures. One announcement may produce an HTML file and several attachments, so announcement and file counts differ.

The GUI shows recent collections and can reopen saved results after a server restart. Interrupted **agent execution is not resumed** automatically; start a new run to retry. Reusing extraction recipes and incremental updates are future enhancements.

## Boundaries of this prototype

- There is no run-wide action, document-count, cumulative token, or time limit. Collection continues until the agent finishes, an API/network error ends it, or you press **Stop**. The elapsed timer updates every second, includes time awaiting clarification, and freezes at completion/cancellation/failure. Start/finish times and duration are saved in the catalogue; old runs show an estimate from their final activity. Individual operations retain request timeouts, per-response token sizes, a 50 MB file limit, and bounded recovery attempts.
- Search is hosted by OpenAI; Firecrawl is not required. The key must have access to `gpt-5-mini`, Responses, and web search. Model/tool availability is checked by the provider during requests; errors are shown without silently changing models.
- Robots restrictions are respected for direct page navigation/downloads. Access challenges, login walls, inaccessible robots files, encrypted/scanned PDFs, session-bound downloads, unusual viewers, and complex controls can leave gaps. No OCR or CAPTCHA bypass is implemented.
- HTML archives are paginated in the model's observations, but queue enumeration uses the full captured page. The generic detector can miss unusual layouts; the model must inspect coverage and use selectors/browser controls when necessary. Archive status combines code-enforced queue accounting and model review of source coverage; it is not proof of exhaustive website coverage. There is no company-specific scraper in production.
- The browser has an isolated session and blocks local/private network URLs. The local server binds only to `127.0.0.1`, rejects unexpected Host headers, and requires a session token for mutations. Do not expose this prototype to the internet or use it as a multi-user service.
- Progress logs show actions and observable outcomes, not private model reasoning. Search queries appear immediately when requested; hosted search details arrive when its API call returns.
- API keys are not persisted through the GUI; secrets in a user-created `.env` file remain on disk by choice.

## Development

```sh
uv run pytest
```

Tests use fake model/network responses: they do not consume OpenAI credits. They include a replay of 11 real links captured in the Singtel test run, exact encoded URLs, publication-row dates, unrelated-row rejection, bounded recovery, large archive lookup, provenance, deduplication, clarification, collection beyond the old action/token ceilings, timer persistence, cancellation, and API exports. The Singtel fixture is test data only; production contains no company-specific routing. A live end-to-end check requires a valid key and network access.

An optional focused live check uses **one paid model call** and two real Singtel PDF downloads: `.venv/bin/python scripts/live_link_smoke.py`. It selects IDs from saved source evidence, validates the real tool schema and download path, and removes its temporary output. It does not test fresh company discovery or exhaustive archive coverage.

An optional identity-policy check uses **three paid model calls**: `.venv/bin/python scripts/live_identity_smoke.py`. It supplies synthetic post-search observations to check a location-qualified name, a place word inside a Malaysian company's name, and a genuinely ambiguous bare name. It tests model decisions against controlled evidence, not live company facts.

An optional live collector regression uses **no model calls**: `.venv/bin/python scripts/live_queue_smoke.py`. It starts from JUMBO's already established official IR archive, traverses the observed year pages, downloads announcements and their attachments into a temporary folder, then writes `docs/jumbo-queue-validation.json` and removes the temporary originals. The fixed comparison window is 1 November 2024–1 November 2026. It tests collection, not fresh company discovery. Automated offline tests also cover a 71-announcement archive, year/pagination cycles, links beyond excerpts, undated selector-based extraction, embedded tracking destinations, failures, and cancellation.

Code: `webagent/agent.py` (agent loop), `webagent/collector.py` (enumeration and queue), `webagent/web.py` (fetching), `webagent/browser.py` (browser tools), `webagent/state.py` (catalogue), `webagent/server.py` (local API), and `static/` (GUI).

## Three-module workspace

The shared UI now includes Module 3, **External environment**, mounted at `/external/`. Module 2 can pass a selected output folder to Module 3 through the UI; running either module remains a separate user action. The presentation layer contains no pipeline sequencing. See `modules/external_research/README.md` and `contract.json` for standalone use and the external research evidence contract.

Module 2 additionally exports research questions, question coverage, evidence comparisons and atomic claim checks. Its parsing preserves layout/table text; it does not yet use Docling or OCR. Neither module uses GraphRAG.
