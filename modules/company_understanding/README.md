# Module 2 — Company Understanding

A standalone program that turns a local document collection into a cited company profile, significant changes, potential business exposures, and open questions. Uses **GPT-5 mini** by default. It does not import Module 1, call its server, collect new web documents, perform macroeconomic research, or produce audit-risk conclusions.

The GUI, CLI and Python interface are adapters to the same module. A future orchestrator remains a **separate application**: it decides which modules to call and in what order. This module does not start any other module. It can be copied into a separate repository with its own dependencies and configuration.

## Run this module independently

For the shared two-module UI, run `uv run python run.py` from the WebAgent project root and open **http://127.0.0.1:8765**. The workspace provides a saved-collection selector and manual folder handoff; it does not orchestrate jobs. The independent launch below remains available.

From this directory:

```sh
uv sync
cp .env.example .env
# Set OPENAI_API_KEY in this module's .env, or enter it in the GUI.
uv run python -m company_understanding serve
```

Open **http://127.0.0.1:8766**. Module 1 can continue to use port 8765. Select the collection by pasting its absolute folder path, then enter the financial year-end and information cutoff date. The **synthetic sample** runs the complete workflow with fictional documents and deterministic responses; it does not use the OpenAI API.

For the existing WebAgent development environment, dependencies are already available:

```sh
../../.venv/bin/python -m company_understanding serve
```

Configuration is intentionally independent. This module loads only its own `.env` by default. To explicitly reuse an existing environment file without copying credentials:

```sh
../../.venv/bin/python -m company_understanding --env-file ../../.env serve
```

`OPENAI_MODEL` defaults to `gpt-5-mini`. `UNDERSTANDING_OUTPUT_DIR` controls GUI output; default is `data/` in this module. GUI API keys stay in browser/process memory, never in job files or browser storage. Live runs transmit extracted source text to OpenAI and incur API usage.

## File-based input contract

`contract.json` describes the integration boundary. The input is a folder containing `catalogue.json` and its local documents. It accepts Module 1's existing catalogue, but any program can provide this minimal format:

```json
{
  "company": "Example Company",
  "status": "finished",
  "documents": [
    {
      "id": "annual-2026",
      "title": "Annual report 2026",
      "category": "annual_report",
      "file": "documents/annual-report.pdf",
      "status": "saved",
      "published_date": "2026-10-15",
      "url": "https://example.com/annual-report.pdf"
    }
  ]
}
```

File paths must resolve inside the supplied collection folder. Optional `sha256` is checked before processing. Only the manifest's document paths are read; files elsewhere in the folder are not scanned. A source collection marked running must finish or be stopped before use. Synthetic Module 1 collections are rejected as live evidence.

Documents published after the information cutoff are excluded. Unknown or unverified publication dates remain included with an explicit availability warning; their output items cannot pass the cutoff review. The module does not infer publication dates from filenames or fiscal years. Periods and group/parent/subsidiary scope are retained separately in findings.

## CLI interface for a future orchestrator

```sh
uv run python -m company_understanding run \
  --source /absolute/path/to/collection \
  --financial-year-end 2026-09-30 \
  --cutoff 2026-11-01 \
  --output-dir /absolute/path/to/module2-output
```

The CLI prints a JSON result containing `contract_version`, `job_id`, `status`, `output_folder` and `manifest`. Exit codes: **0** finished; **2** partial; **1** failed/cancelled; **64** invalid input/configuration. Credentials are supplied through environment configuration, not command arguments. The module does not choose the pipeline sequence.

Python callers can use:

```python
from pathlib import Path
from company_understanding.engine import run_job
from company_understanding.models import Request

result = await run_job(
    Request(source_folder="/path/to/collection",
            financial_year_end="2026-09-30",
            information_cutoff="2026-11-01"),
    Path("/path/to/module2-output"),
    api_key=api_key,
    model="gpt-5-mini",
)
```

The optional local HTTP adapter provides `POST /api/jobs`, `GET /api/jobs/{id}`, cancellation, history and exports. Mutating HTTP requests require the local session token from `/api/config`. Prefer the CLI/Python interface for unattended orchestration.

## Processing workflow

1. Freeze the source catalogue, apply cutoff eligibility, hash and deduplicate files.
2. Extract **all** PDF pages and supported document text. Preserve physical PDF page numbers, slide numbers and HTML/text block references. No first-12-page shortcut is used.
3. Read all extracted text in bounded batches and extract facts with exact quotations, periods, entity scopes and document classifications. Annual reports are processed first as baseline evidence; subsequent sources supplement it.
4. Validate each cited chunk ID and quotation against the actual supplied text. Reject statements introducing numerical tokens absent from their quotations. These checks do not establish semantic correctness or verify accounting calculations.
5. Generate potential business exposures and questions from company evidence. Search the full local text for relevant terms and re-read matching excerpts for follow-up evidence. No industry library or vector database is required for this first version.
6. Synthesize the company profile and changes by period. Preserve management attribution and label interpretations as inferences. Unestablished areas become open questions.
7. Regenerate the exposure register from the expanded evidence, then run a separate model review against original quotations, scope, units, dates and chronology. Missing review decisions, unverified publication dates and newly introduced numerical tokens are flagged.
8. Export the job manifest, structured outputs and readable report. The auditor reviews these before using them in planning.

No run-wide action/time/token ceiling is imposed. Individual calls have timeouts, context/output sizes and bounded transport retries. Larger collections may involve many paid model calls. The GUI shows elapsed time, processed documents/chunks, extracted findings and progress. Cancel retains intermediate evidence and marks outputs unreviewed where necessary. Jobs are not automatically resumed after interruption.

## Outputs

```text
data/<job-id>/
  job.json                   Complete state, usage, facts, coverage, review notes
  input_catalogue.json       Snapshot of the source catalogue
  company_profile.json       Business understanding, evidence and review decisions
  significant_changes.json  Historical and subsequent developments
  exposure_register.json    Conditional business exposures, uncertainties, questions
  open_questions.json       Missing information for follow-up
  report.html               Readable report with quotations and source locations
  evidence/chunks.jsonl      Extracted source text, hashes and processing status
```

Every structured deliverable includes contract version, company, dates, job status and the synthetic-sample flag. Evidence references link to source document IDs, file paths, source URLs, physical locations and exact quotations. The original files stay in the input folder; exports include the extracted evidence, not copies of the original PDFs. Keep both folders if original-source verification is needed. The GUI checks source hashes when opening originals.

## Practical limits

- This is preliminary company understanding, not an independently verified audit workpaper. Model review can miss mistakes; numeric-token checks do not verify unit scaling, arithmetic, accounting interpretation or whether a quote logically entails a statement.
- PDF, HTML, TXT, CSV, DOCX and PPTX text extraction is supported. Legacy Office files, spreadsheets and ZIP bundles remain visible as extraction gaps. DOCX references are text sections, not rendered Word page numbers. Slide extraction covers slide text, not speaker notes or images.
- Scanned/image-only content is flagged; no OCR is included. Complex tables may lose layout, so the module flags ambiguity and the auditor should inspect the originals.
- Source coverage includes every catalogue row and every extracted text chunk, but extraction and model synthesis cannot guarantee every relevant fact is identified. Unknown publication dates and missing/unreadable files result in partial outputs.
- The same requested model performs extraction, synthesis and a separate review call. This is a second pass, not independent professional verification.
- The local GUI binds to loopback and is intended for a single user. Do not expose it as a multi-user internet service.

## Verification

```sh
uv run pytest
uv run python -m company_understanding demo --output-dir /tmp/company-understanding-demo
```

Tests use synthetic sources and mocked model/network responses; they use no paid API calls. They cover all-page PDF extraction, HTML/slide locations, cutoff filtering, duplicate files, source hashes/path isolation, fabricated citations, unsupported numbers, cancellation/refusal, real SDK schema serialization, independent API operation and exports. The GUI sample also uses no API calls.

## Question coverage and evidence quality upgrade

Module 2 now preserves PDF layout spacing/lines and HTML table rows/headers instead of flattening every page into one string. DOCX paragraph/table structure is retained as text. This remains pypdf-based extraction: **Docling and OCR are not installed or used**. Complex tables, merged cells, scanned pages and charts still need review.

After initial evidence extraction, it builds baseline and company-specific research questions, retrieves additional passages, and writes `research_questions.json` and `question_coverage.json`. Each question is answered, conflicting_evidence, or not_established; partially answered questions retain their gaps. Coverage is model-assessed, not proof that every relevant business issue has been identified.

`evidence_comparisons.json` distinguishes conflicts, changes over time and different entity/scope disclosures across areas within compared evidence batches. Large collections are batched, so cross-batch contradictions may remain undetected. `claim_checks.json` records atomic claim questions, independent answers from original passages, and a subsequent comparison against the draft. Invalid quotations, unknown cutoff availability and failed verification prevent a supported verdict. Same-model verification is not independent audit assurance.

The shared UI has Question coverage, Evidence comparisons and Claim checks tabs. Original module outputs and file contract version 1.0 remain compatible; these outputs are additive. No GraphRAG or cross-module orchestration was added.

`evaluation/cases.json` contains eight hand-labelled synthetic review cases covering scope, units, periods, exclusivity, management attribution and contradictions. To run the **paid** model check from this directory:

```sh
../../.venv/bin/python evaluation/evaluate.py --env-file ../../.env
```

This reports evidence-review classification accuracy on those eight cases. It is a small regression check, not a measurement of end-to-end recall, extraction accuracy or audit quality. Automated offline tests separately check extraction structure, invalid citations, coverage states, comparisons, cancellation and API exports.
