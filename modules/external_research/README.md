# Module 3 — External Environment Research

Independent economic and industry research based on cited company understanding. Uses GPT-5 mini and OpenAI web search. It imports neither the collector nor Module 2 and contains no cross-module orchestration. It can run on its own from a saved Module 2 output folder.

## Shared UI

From the WebAgent root, run `uv run python run.py` and open http://127.0.0.1:8765. Select **03 · External environment**. Choose a completed/partial Module 2 output, click **Use output**, enter the audit period and information cutoff, then start. Selecting an input never starts a paid run automatically.

## Independent setup

```sh
uv sync
cp .env.example .env
# Set OPENAI_API_KEY in .env
uv run python -m external_research serve
```

Open http://127.0.0.1:8767. Its own .env and output directory are independent. The shared workspace uses the root environment and this module's distinct default data directory.

```sh
uv run python -m external_research run --source /absolute/module2/output --audit-period-start 2025-10-01 --audit-period-end 2026-09-30 --cutoff 2026-11-01
uv run python -m external_research demo
uv run pytest
```

CLI exits 0 for finished, 2 for partial, 1 for failed/cancelled; argparse input errors exit 2. The Python interface is `await run_job(request, output_root, api_key=..., model='gpt-5-mini')`.

## Workflow and evidence

1. Freeze Module 2 input; preserve company citations, scope, timing and review status.
2. Plan topics linked to actual company evidence, relevant industries/geographies and preferred authoritative publishers.
3. Search each topic with audit dates and information cutoff. Save search responses. Only provider URL citation annotations supply downloadable URLs; model-written prose URLs are not trusted.
4. Fetch cited public HTML/PDF sources, with public IP checks, pinned destination addresses and redirect validation. Save original bytes, SHA-256 and extracted text/page locations. All source text segments are processed; image-only PDFs remain gaps. Search results are discovery leads, never substituted for an inaccessible source's evidence.
5. Extract relevant observations, requiring quotes that match captured text and numbers present in those quotes. Keep measurement periods, geography, units and publication dates separate. Exclude observations whose quoted publication dates are after cutoff. Unknown dates remain unverified and force partial status.
6. Explain conditional company implications using both external observations and company evidence. Preserve conflicts, geographic limitations and missing information. Review implications against both evidence sets; unresolved Module 2 review status propagates.
7. Export JSON files, a readable HTML report, source snapshots and a ZIP. The module does not rate audit risks or prescribe audit procedures.

Source type and publication-date interpretation are model-assisted. Matching text/date checks cannot prove a source is correct or that a current webpage was unchanged at the cutoff. Revisions, ambiguous dates, selective search coverage, dynamic pages, blocked pages, parsing problems and limited source coverage can leave gaps. This is not exhaustive research or an archived historical web reconstruction. There is no browser rendering, OCR or login/CAPTCHA bypass in Module 3.

Research topics and source counts are model-selected; there is no global action/time budget. Per-response output limits, request timeouts, redirect bounds and a 25 MB per-source download limit remain. Individual source failures do not discard earlier evidence. Stop saves partial work; stopped jobs do not automatically resume. Model/search usage is recorded, but no dollar estimate is calculated.

Live runs send company evidence to OpenAI, public topic queries to its web search, and fetch public websites directly. Input API keys are not persisted in jobs or browser storage. The synthetic sample uses fictional company/country/statistics and no network calls. Review all evidence before using findings in audit planning.

A Module 3 cutoff earlier than the input Module 2 cutoff is rejected, to avoid using company information that was not available then. Different Module 2 and audit-period year-ends are explicitly flagged for historical applicability review.
