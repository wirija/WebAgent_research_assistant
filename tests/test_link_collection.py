import json
from datetime import date
from pathlib import Path

from webagent.agent import Agent, TOOLS
from webagent.dates import link_date
from webagent.web import Fetch, extract_html
from test_collection import FakeWeb, identify, setup_agent


def selection(lid, **changes):
    return {"link_id": lid, "title": "Annual report", "category": "annual_report",
            "published_date": None, "date_evidence": ""} | changes


async def test_singtel_replay_uses_original_urls_and_publication_rows(tmp_path, monkeypatch):
    monkeypatch.setattr("webagent.state.today", lambda: date(2026, 11, 1))
    run, agent, _ = setup_agent(tmp_path)
    data = json.loads((Path(__file__).parent / "fixtures/singtel_links.json").read_text())
    oid = agent.observe(data.pop("url"), data)
    await agent.set_company("Singtel", "https://www.singtel.com/", oid, "Saved official website observation")
    # Each URL gets distinct bytes, so this tests saving every source rather than dedup.
    async def fetch(url, **kwargs):
        agent.web.calls.append(url)
        return Fetch(url, b"%PDF-1.4\n" + url.encode(), "application/pdf")
    agent.web.fetch = fetch
    links = (await agent.list_links(oid, "", 0))["links"]
    result = await agent.collect([selection(x["link_id"], title=x["context"],
                                date_evidence="AI paraphrase that is not a quote") for x in links])
    assert len(links) >= 3
    assert all(d["status"] == "saved" for d in result["results"])
    assert agent.web.calls == [x["url"] for x in links]
    assert all(d["date_method"] == "publication_row" for d in run.documents)
    gulf = next(d for d in run.documents if "Gulf" in d["title"])
    assert gulf["published_date"] == "2026-09-10"
    annual = next(d for d in run.documents if "Annual Report 2026," in d["title"])
    assert annual["published_date"] == "2026-06-30"
    assert "AR&LTS&OtherDocs_sgx.pdf" in annual["url"]
    assert not run.issues


async def test_ids_preserve_encoded_url_and_survive_refresh(tmp_path):
    run, agent, oid = setup_agent(tmp_path)
    await identify(agent, oid)
    url = 'https://cdn.example/Report%20A.pdf?name=A%26B&token=abc%2F123'
    data = extract_html(f'<table><tr><td>Quarter ended 30 June 2026</td><td>13 Aug 2026</td><td><a href="{url.replace("&", "&amp;")}">PDF</a></td></tr></table>', 'https://company.example/ir')
    first = agent.observe('https://company.example/ir', data)
    second = agent.observe('https://company.example/ir', data)
    lid = agent.observations[first]['links'][0]['link_id']
    assert lid == agent.observations[second]['links'][0]['link_id']
    run.start_date, run.end_date = date(2024, 11, 1), date(2026, 11, 1)
    result = (await agent.collect([selection(lid)]))['results'][0]
    assert result['status'] == 'saved'
    assert result['published_date'] == '2026-08-13'
    assert agent.web.calls == [url]
    spec = next(t for t in TOOLS if t['name'] == 'collect_documents')
    assert 'url' not in spec['parameters']['properties']['documents']['items']['properties']


async def test_unknown_id_cannot_invent_url(tmp_path):
    run, agent, oid = setup_agent(tmp_path)
    await identify(agent, oid)
    result = await agent.collect([selection('link-invented')])
    assert 'Unknown link ID' in result['results'][0]['error']
    assert not agent.web.calls


async def test_other_rows_date_is_not_accepted(tmp_path):
    run, agent, oid = setup_agent(tmp_path)
    await identify(agent, oid)
    data = extract_html('<ul><li><a href="/undated.pdf">Annual report 2026</a></li><li>2026-05-21 <a href="/dated.pdf">Results</a></li></ul>', 'https://company.example/ir')
    oid = agent.observe('https://company.example/ir', data)
    result = (await agent.collect([selection(agent.observations[oid]['links'][0]['link_id'],
             published_date='2026-05-21', date_evidence='2026-05-21')]))['results'][0]
    assert result['status'] == 'saved' and result['published_date'] is None
    assert result['date_status'] == 'needs_review'
    assert link_date({'context':'Results for the quarter ended 30 June 2026'}) == (None, '')
    assert link_date({'context':'Results for the quarter ended 30 June 2026 PDF', 'label':'PDF'}) == (None, '')
    assert link_date({'date_context':'03/04/2026'}) == (None, '')


async def test_source_refresh_recovers_download(tmp_path):
    run, agent, oid = setup_agent(tmp_path)
    await identify(agent, oid)
    lid = agent.observations[oid]['links'][0]['link_id']
    doc_calls = []
    async def fetch(url, **kwargs):
        if url.endswith('/ir'):
            return Fetch(url, b'<a href="/report.pdf">Report</a>', 'text/html')
        doc_calls.append(url)
        if len(doc_calls) == 1:
            raise ValueError('Temporary download failure')
        return Fetch(url, b'%PDF-1.4\nRecovered', 'application/pdf')
    agent.web.fetch = fetch
    result = (await agent.collect([selection(lid)]))['results'][0]
    assert result['status'] == 'saved' and len(result['attempts']) == 2
    assert len(run.documents) == 1
    assert result['attempts'][0]['error'] == 'Temporary download failure'
    assert result['error'] is None
    assert run.counts()['failed'] == 0 and run.counts()['downloaded'] == 1


async def test_date_can_be_verified_after_file_is_saved_without_redownload(tmp_path):
    run, agent, oid = setup_agent(tmp_path)
    await identify(agent, oid)
    lid = agent.observations[oid]['links'][0]['link_id']
    # Simulate a genuinely undated link; simple date + title rows are now
    # recognized automatically.
    agent.links[lid]['context'] = f'Published on {run.end_date}: Results'
    agent.links[lid]['date_context'] = ''
    record = (await agent.collect([selection(lid)]))['results'][0]
    assert record['date_status'] == 'needs_review'
    result = (await agent.collect([selection(lid, published_date=str(run.end_date), date_evidence=str(run.end_date))]))['results'][0]
    assert result['published_date'] == str(run.end_date) and result['date_status'] == 'source_quote_present'
    assert len(agent.web.calls) == 1 and len(run.documents) == 1
    assert not run.issues


async def test_cached_read_avoids_duplicate_fetch_but_refresh_fetches(tmp_path):
    run, agent, oid = setup_agent(tmp_path)
    assert (await agent.read('https://company.example/ir'))['observation_id'] == oid
    assert agent.web.calls == []
    async def fetch(url, **kwargs):
        agent.web.calls.append(url)
        return Fetch(url, b'<a href="/new.pdf">New report</a>', 'text/html')
    agent.web.fetch = fetch
    fresh = await agent.read('https://company.example/ir', refresh=True)
    assert fresh['observation_id'] != oid and len(agent.web.calls) == 1


async def test_recovery_is_bounded_and_exposes_browser_observation(tmp_path):
    run, agent, oid = setup_agent(tmp_path)
    await identify(agent, oid)
    lid = agent.observations[oid]['links'][0]['link_id']
    doc_calls = []
    html = '<a href="/report.pdf">Report</a><button>Download PDF</button>'
    async def fetch(url, **kwargs):
        if url.endswith('/ir'):
            return Fetch(url, html.encode(), 'text/html')
        doc_calls.append(url)
        raise ValueError('File unavailable')
    async def render(url):
        return url, html, [{'id':0, 'label':'Download PDF'}]
    agent.web.fetch = fetch
    agent.browser.open = render
    first = (await agent.collect([selection(lid)]))['results'][0]
    second = (await agent.collect([selection(lid)]))['results'][0]
    assert first['status'] == second['status'] == 'failed'
    assert len(doc_calls) == 3
    assert len(run.documents) == 1 and run.counts()['failed'] == 1
    assert len(first['recovery_observations']) == 2
    assert 'browser_action' in first['next_action']


async def test_archive_lookup_reaches_links_beyond_excerpt(tmp_path):
    run, agent, oid = setup_agent(tmp_path)
    html = ''.join(f'<li><a href="/report-{i}.pdf">Report {i}</a></li>' for i in range(130))
    oid = agent.observe('https://company.example/ir', extract_html(html, 'https://company.example/ir'))
    result = await agent.list_links(oid, 'Report 129', 0)
    assert len(result['links']) == 1 and result['links'][0]['url'].endswith('report-129.pdf')
    assert (await agent.list_links(oid, '', 0))['next_offset'] == 50
    assert (await agent.list_links(oid, '', 100))['next_offset'] is None
    assert len(run.pages[-1]['link_scans']) == 3


async def test_finish_collects_unselected_dated_links(tmp_path):
    run, agent, oid = setup_agent(tmp_path)
    await identify(agent, oid)
    html = f'<table><tr><td>{run.end_date}</td><td><a href="/uncollected.pdf">Report</a></td></tr></table>'
    agent.observe('https://company.example/ir', extract_html(html, 'https://company.example/ir'))
    await agent.register_archive('https://company.example/ir', 'reports')
    await agent.update_archive('https://company.example/ir', 'checked', 'Inspected archive')
    # Other observations still contain the fixture's failure link: finish must
    # attempt it and report failure, never silently omit it.
    assert (await agent.finish('Done', []))['status'] == 'partial'
    assert any(d['url'].endswith('/uncollected.pdf') and d['status'] == 'saved' for d in run.documents)
    assert any(d['url'].endswith('/failure.pdf') and d['status'] == 'failed' for d in run.documents)
