import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from webagent.agent import Agent, detect_extension, date_matches
from webagent.state import DocumentInput, Run, RunRequest
from webagent.web import Fetch, canonical, extract_html, validate_public_url


class FakeWeb:
    def __init__(self):
        self.calls = []
        self.closed = False

    async def fetch(self, url, **kwargs):
        self.calls.append(url)
        if "failure" in url:
            raise ValueError("Simulated network failure")
        return Fetch(url, b"%PDF-1.4\nsynthetic fixture bytes", "application/pdf")

    async def close(self):
        self.closed = True


class Item:
    def __init__(self, name, args, index=0):
        self.type = "function_call"
        self.name = name
        self.arguments = json.dumps(args)
        self.call_id = f"call-{index}"

    def model_dump(self, **kwargs):
        return vars(self)


class FakeClient:
    def __init__(self, calls=()):
        self.calls = iter(calls)
        self.requests = []
        self.responses = self
        self.closed = False

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        item = next(self.calls, Item("register_archive", {"url": "https://company.example/ir", "category": "results"}, len(self.requests)))
        return SimpleNamespace(output=[item], usage=SimpleNamespace(input_tokens=20, output_tokens=10))

    async def close(self):
        self.closed = True


def setup_agent(tmp_path, **kwargs):
    run = Run(RunRequest(company="Any Company", **kwargs), tmp_path)
    agent = Agent(run, "unused-test-key", client=FakeClient(), web=FakeWeb())
    url = "https://company.example/ir"
    date = str(run.end_date)
    data = extract_html(f'<title>Official IR</title><ul><li>{date}<a href="/report.pdf">Results</a></li><li>{date}<a href="/copy.pdf">Copy</a></li><li>{date}<a href="/failure.pdf">Failure</a></li></ul>', url)
    oid = agent.observe(url, data)
    return run, agent, oid


async def identify(agent, oid):
    await agent.set_company("Any Company Ltd", "https://company.example/ir", oid, "Company identity shown on inspected page")


def document(run, oid, url="https://company.example/report.pdf", **changes):
    return DocumentInput(**({"title": "Results", "category": "results", "url": url, "observation_id": oid,
                            "published_date": run.end_date, "date_evidence": str(run.end_date)} | changes))


async def test_collection_deduplicates_and_preserves_sources(tmp_path):
    run, agent, oid = setup_agent(tmp_path)
    await identify(agent, oid)
    a = await agent.save_document(document(run, oid))
    b = await agent.save_document(document(run, oid, "https://company.example/copy.pdf"))
    assert a["status"] == "saved" and b["status"] == "duplicate"
    assert b["duplicate_of"] == a["id"]
    assert (run.folder / a["file"]).read_bytes().startswith(b"%PDF")
    assert len(list((run.folder / "documents").iterdir())) == 1
    run.finalize()
    saved = json.loads((run.folder / "catalogue.json").read_text())
    assert saved["documents"][0]["source_url"] == "https://company.example/ir"
    assert "unused-test-key" not in (run.folder / "catalogue.json").read_text()


async def test_unobserved_and_unofficial_links_rejected(tmp_path):
    run, agent, oid = setup_agent(tmp_path)
    await identify(agent, oid)
    with pytest.raises(ValueError, match="not observed"):
        await agent.save_document(document(run, oid, "https://company.example/invented.pdf"))
    third = agent.observe("https://unrelated.example/", extract_html('<a href="https://unrelated.example/report.pdf">Report</a>', "https://unrelated.example/"))
    with pytest.raises(ValueError, match="official website"):
        await agent.save_document(document(run, third, "https://unrelated.example/report.pdf"))


async def test_official_linked_cdn_is_allowed(tmp_path):
    run, agent, oid = setup_agent(tmp_path)
    await identify(agent, oid)
    oid = agent.observe("https://company.example/ir", extract_html(f'<p>{run.end_date}<a href="https://cdn.example/report.pdf">Report</a></p>', "https://company.example/ir"))
    record = await agent.save_document(document(run, oid, "https://cdn.example/report.pdf"))
    assert record["status"] == "saved"


@pytest.mark.parametrize("boundary,expected", [("start", "saved"), ("end", "saved"), ("before", "excluded_date"), ("after", "excluded_date")])
async def test_publication_window_boundaries(tmp_path, boundary, expected):
    run, agent, oid = setup_agent(tmp_path)
    await identify(agent, oid)
    value = {"start": run.start_date, "end": run.end_date, "before": run.start_date-timedelta(days=1), "after": run.end_date+timedelta(days=1)}[boundary]
    agent.observations[oid]["text"] += f" Published {value}"
    record = await agent.save_document(document(run, oid, published_date=value, date_evidence=str(value)))
    assert record["status"] == expected
    assert bool(agent.web.calls) == (expected == "saved")


async def test_missing_date_evidence_keeps_file_for_review(tmp_path):
    run, agent, oid = setup_agent(tmp_path)
    await identify(agent, oid)
    record = await agent.save_document(document(run, oid, published_date=run.start_date-timedelta(days=100), date_evidence="invented date"))
    assert record["status"] == "saved"
    assert record["published_date"] is None and record["date_status"] == "needs_review"
    assert run.issues


async def test_failed_download_retained_and_retry_reuses_record(tmp_path):
    run, agent, oid = setup_agent(tmp_path)
    await identify(agent, oid)
    doc = document(run, oid, "https://company.example/failure.pdf")
    assert (await agent.save_document(doc))["status"] == "failed"
    assert (await agent.save_document(doc))["status"] == "failed"
    assert len(run.documents) == 1


async def test_clarification_pauses_and_continues(tmp_path):
    run, agent, oid = setup_agent(tmp_path)
    run.event("search", "Any Company official identity candidates")
    task = asyncio.create_task(agent.ask_user("Which Jumbo do you mean?"))
    await asyncio.sleep(0)
    assert run.status == "needs_input" and run.question
    await agent.answer.put("Jumbo Group, Singapore")
    assert (await task)["user_answer"] == "Jumbo Group, Singapore"
    assert run.status == "running" and not run.question


async def test_identity_questions_require_research_and_not_confirmation(tmp_path):
    run, agent, oid = setup_agent(tmp_path)
    with pytest.raises(ValueError, match="Search the full user phrase"):
        await agent.ask_user("Which country?")
    assert run.question is None
    run.event("search", "Any Company official identity candidates")
    await identify(agent, oid)
    with pytest.raises(ValueError, match="already resolved"):
        await agent.ask_user("Please confirm this company?")
    assert run.question is None


async def test_agent_loop_preserves_tool_outputs_and_finishes(tmp_path):
    run, agent, oid = setup_agent(tmp_path)
    async def success(url, **kwargs):
        return Fetch(url, b"%PDF-1.4\n" + url.encode(), "application/pdf")
    agent.web.fetch = success
    await identify(agent, oid)
    agent.client = FakeClient([
        Item("register_archive", {"url": "https://company.example/ir", "category": "results"}, 1),
        Item("collect_documents", {"documents": [{"link_id": agent.observations[oid]["links"][0]["link_id"],
             "title": "Results", "category": "results", "published_date": str(run.end_date), "date_evidence": str(run.end_date)}]}, 2),
        Item("update_archive", {"url": "https://company.example/ir", "status": "checked", "evidence": "Inspected fixture archive and all rows"}, 3),
        Item("finish", {"summary": "Collected the fixture report.", "gaps": []}, 4),
    ])
    await agent.execute()
    assert run.status == "finished"
    assert run.documents[0]["status"] == "saved"
    assert run.usage["model_calls"] == 4
    assert any(x.get("type") == "function_call_output" for x in agent.history)
    assert agent.web.closed and agent.client.closed
    assert (run.folder / "summary.txt").exists()


async def test_collection_continues_beyond_old_action_and_token_limits(tmp_path):
    run, agent, oid = setup_agent(tmp_path)
    async def success(url, **kwargs):
        return Fetch(url, b"%PDF-1.4\n" + url.encode(), "application/pdf")
    agent.web.fetch = success
    await identify(agent, oid)
    url = "https://company.example/ir"
    await agent.register_archive(url, "results")
    agent.client = FakeClient([
        *[Item("update_archive", {"url": url, "status": "checked", "evidence": "Inspected source"}, i) for i in range(65)],
        Item("finish", {"summary": "Finished the fixture exploration.", "gaps": []}, 66),
    ])
    run.usage.update(input_tokens=1_200_001, output_tokens=100_001)
    await agent.execute()
    assert agent.finished and run.step == 66
    assert not any("limit" in x for x in run.issues)
    assert "max_steps" not in run.snapshot() and "max_documents" not in run.snapshot()


async def test_cancel_retains_manifest(tmp_path):
    run2, agent2, oid2 = setup_agent(tmp_path)
    run2.event("search", "Any Company official identity candidates")
    agent2.client = FakeClient([Item("ask_user", {"question": "Which company?"})])
    task = asyncio.create_task(agent2.execute())
    await asyncio.sleep(0)
    task.cancel()
    await task
    assert run2.status == "cancelled"
    assert (run2.folder / "catalogue.json").exists()


def test_html_parser_keeps_context_and_pagination():
    data = extract_html('<title>Reports</title><table><tr><td>1 Nov 2026</td><td><a href="/report.pdf">Report</a></td></tr></table><a href="?page=2">Next</a>', "https://example.com/ir")
    assert data["links"][0]["url"] == "https://example.com/report.pdf"
    assert "1 Nov 2026" in data["links"][0]["context"]
    assert data["links"][1]["url"] == "https://example.com/ir?page=2"


def test_rejects_error_html_disguised_as_pdf():
    with pytest.raises(ValueError, match="returned HTML"):
        detect_extension(b"<html>Error</html>", "text/html", "https://example.com/report.pdf")
    with pytest.raises(ValueError, match="challenge"):
        detect_extension(b"<html>Verify you are human</html>", "text/html", "https://example.com/news")


async def test_local_network_is_not_browsable():
    with pytest.raises(ValueError, match="Private"):
        await validate_public_url("http://127.0.0.1/report")
    with pytest.raises(ValueError, match="credentials"):
        canonical("https://user:pass@example.com/")
    with pytest.raises(ValueError, match="ports"):
        canonical("https://example.com:8000/")


def test_long_observation_has_pagination(tmp_path):
    run, agent, oid = setup_agent(tmp_path)
    agent.observations[oid]["text"] = "a"*21000
    assert agent.excerpt(oid)["has_more"]
    assert len(agent.excerpt(oid, 1)["text"]) == 10000
    assert not agent.excerpt(oid, 2)["has_more"]


def test_date_quote_must_support_normalized_date():
    from datetime import date
    assert date_matches(date(2026, 5, 21), "Published 21 May 2026")
    assert date_matches(date(2026, 5, 21), "May 21, 2026")
    assert not date_matches(date(2026, 5, 21), "Annual report 2026")
    assert not date_matches(date(2026, 4, 3), "03/04/2026")
    assert not date_matches(date(2026, 5, 21), "22 May 2026")


async def test_search_uses_requested_model_and_actual_sdk_serialization(tmp_path):
    import httpx
    from openai import AsyncOpenAI
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "resp_test", "object": "response", "created_at": 1, "status": "completed", "model": "gpt-5-mini",
            "output": [
                {"id": "ws_test", "type": "web_search_call", "status": "completed", "action": {"type": "search", "query": "company", "sources": [{"type": "url", "url": "https://company.example/", "title": "Company"}]}},
                {"id": "msg_test", "type": "message", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "text": "Official company website.", "annotations": []}]}
            ], "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}})
    run, agent, oid = setup_agent(tmp_path)
    agent.client = AsyncOpenAI(api_key="sk-test-only", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    result = await agent.search("Any company official website")
    assert result["observed_urls"] == ["https://company.example/"]
    assert requests[0]["model"] == "gpt-5-mini" and requests[0]["tools"] == [{"type": "web_search"}]
    assert run.usage["search_calls"] == 1
    await agent.client.close()


async def test_browser_rejects_stale_element_ids(tmp_path):
    run, agent, oid = setup_agent(tmp_path)
    with pytest.raises(ValueError, match="Stale"):
        await agent.browser.act("old-id", "click", 5, None)
