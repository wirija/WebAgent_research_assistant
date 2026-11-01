from __future__ import annotations

import csv
import json
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from pydantic import BaseModel, Field, SecretStr


def today() -> date:
    return datetime.now(ZoneInfo("Asia/Singapore")).date()


def slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")[:80] or "company"


class RunRequest(BaseModel):
    company: str = Field(min_length=2, max_length=160)
    api_key: SecretStr | None = None
    months: int = Field(default=24, ge=1, le=60)
    demo: bool = False


class DocumentInput(BaseModel):
    url: str
    observation_id: str
    title: str = Field(min_length=1, max_length=400)
    category: Literal["results", "annual_report", "presentation", "press_release", "announcement", "other_financial"]
    published_date: date | None
    date_evidence: str = Field(max_length=600)


class Run:
    def __init__(self, request: RunRequest, root: Path, model: str = "gpt-5-mini"):
        self.id = uuid.uuid4().hex
        self.company = request.company.strip()
        self.model = model
        self.demo = request.demo
        self.started_at = datetime.now(ZoneInfo("Asia/Singapore")).isoformat()
        self.finished_at: str | None = None
        self.end_date = today()
        self.start_date = self.end_date - relativedelta(months=request.months)
        self.folder = root / f"{slug(self.company)}-{self.started_at[:10]}-{self.id[:8]}"
        (self.folder / "documents").mkdir(parents=True)
        (self.folder / "evidence").mkdir()
        self.status = "running"
        self.stage = "Starting"
        self.events: list[dict] = []
        self.documents: list[dict] = []
        self.pages: list[dict] = []
        self.archives: dict[str, dict] = {}
        self.queue: dict[str, dict] = {}
        self.identity: dict = {}
        self.question: str | None = None
        self.issues: list[str] = []
        self.summary = ""
        self.step = 0
        self.usage = {"input_tokens": 0, "output_tokens": 0, "model_calls": 0, "search_calls": 0}
        self.save()

    def event(self, kind: str, message: str, url: str | None = None):
        self.events.append({"id": len(self.events), "time": datetime.now(ZoneInfo("Asia/Singapore")).isoformat(),
                            "kind": kind, "message": message, "url": url})
        self.save()

    def issue(self, message: str):
        if message not in self.issues:
            self.issues.append(message)
        self.event("warning", message)

    def snapshot(self) -> dict:
        return {"id": self.id, "company": self.company, "model": self.model, "demo": self.demo,
                "started_at": self.started_at, "finished_at": self.finished_at,
                "elapsed_seconds": elapsed_seconds(self.started_at, self.finished_at),
                "start_date": str(self.start_date), "end_date": str(self.end_date),
                "status": self.status, "stage": self.stage, "step": self.step,
                "folder": str(self.folder.resolve()), "events": self.events,
                "documents": self.documents, "pages": self.pages, "archives": self.archives,
                "queue": self.queue, "queue_counts": self.queue_counts(),
                "identity": self.identity, "question": self.question, "issues": self.issues,
                "summary": self.summary, "usage": self.usage, "counts": self.counts(),
                "coverage_note": "Prototype collection. Exhaustive coverage of the company website is not independently verified."}

    def counts(self):
        return {"identified": len(self.documents),
                "attempted": sum(bool(d.get("attempts")) or d["status"] in {"saved", "duplicate", "failed"} for d in self.documents),
                "downloaded": sum(d["status"] == "saved" for d in self.documents),
                "duplicates": sum(d["status"] == "duplicate" for d in self.documents),
                "failed": sum(d["status"] == "failed" for d in self.documents),
                "date_review": sum(d.get("date_status") == "needs_review" for d in self.documents),
                "excluded": sum(d["status"] == "excluded_date" for d in self.documents)}

    def queue_counts(self):
        return {"total": len(self.queue),
                **{status: sum(x["status"] == status for x in self.queue.values())
                   for status in ("pending", "processing", "completed", "excluded_date", "failed", "interrupted")},
                "announcements": sum(x["kind"] == "document" and not x["parents"] for x in self.queue.values()),
                "attachments": sum(x["kind"] == "document" and bool(x["parents"]) for x in self.queue.values()),
                "archive_pages": sum(x["kind"] == "archive" for x in self.queue.values())}

    def save(self):
        target = self.folder / "catalogue.json"
        tmp = self.folder / "catalogue.tmp"
        tmp.write_text(json.dumps(self.snapshot(), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)

    def finalize(self):
        if self.finished_at is None:
            self.finished_at = datetime.now(ZoneInfo("Asia/Singapore")).isoformat()
        for item in self.queue.values():
            if item["status"] in {"pending", "processing"}:
                item.update(status="interrupted", error="Collection stopped before this item completed.")
        for document in self.documents:
            if document["status"] == "pending":
                document.update(status="failed", error="Interrupted before the download finished.")
                for attempt in document.get("attempts", []):
                    if attempt["status"] == "pending":
                        attempt.update(status="failed", error=document["error"])
        self.save()
        columns = ["title", "category", "published_date", "date_status", "status", "url", "source_url", "file", "duplicate_of", "error"]
        with (self.folder / "catalogue.csv").open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for record in self.documents:
                # Prevent spreadsheet formula interpretation when opening this export.
                writer.writerow({k: ("'" + str(v) if str(v).startswith(("=", "+", "-", "@")) else v)
                                 for k, v in record.items()})
        saved = sum(d["status"] == "saved" for d in self.documents)
        text = (f"COMPANY DOCUMENT COLLECTION\n{self.company}\n\n"
                f"Status: {self.status}\nWindow: {self.start_date} to {self.end_date} (publication dates)\n"
                f"Model: {self.model}\nDemo: {self.demo}\nElapsed seconds: {self.snapshot()['elapsed_seconds']:.1f}\nUnique files saved: {saved}\n\n"
                f"{self.summary}\n\nCOVERAGE\n{self.snapshot()['coverage_note']}\n\n"
                "REVIEW ITEMS\n" + "\n".join(f"- {issue}" for issue in self.issues))
        (self.folder / "summary.txt").write_text(text, encoding="utf-8")

    def context(self) -> str:
        # Explicit durable memory; only concise metadata returns to the model.
        return json.dumps({"identity": self.identity, "archives": self.archives, "step": self.step,
                           "queue_counts": self.queue_counts(),
                           "saved": [{"title": d["title"], "url": d["url"], "link_id": d.get("link_id"), "status": d["status"]} for d in self.documents],
                           "issues": self.issues[-15:]}, ensure_ascii=False)


def elapsed_seconds(started_at: str, finished_at: str | None = None) -> float:
    start = datetime.fromisoformat(started_at)
    end = datetime.fromisoformat(finished_at) if finished_at else datetime.now(ZoneInfo("Asia/Singapore"))
    return max(0.0, (end - start).total_seconds())
