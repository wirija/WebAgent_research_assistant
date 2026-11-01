import asyncio
import json
from datetime import date

import pytest

from webagent.agent import Agent
from webagent.dates import link_date
from webagent.state import Run, RunRequest
from webagent.web import Fetch, extract_html
from test_collection import FakeClient


class Site:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    async def fetch(self, url, **kwargs):
        self.calls.append(url)
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        body = value.encode() if isinstance(value, str) else value
        return Fetch(url, body, 'application/pdf' if body.startswith(b'%PDF') else 'text/html')


async def agent_for(tmp_path, pages, root='https://company.example/newsroom'):
    run = Run(RunRequest(company='Example'), tmp_path)
    run.start_date, run.end_date = date(2024, 11, 1), date(2026, 11, 1)
    agent = Agent(run, 'unused', client=FakeClient(), web=Site(pages))
    oid = agent.observe(root, extract_html(pages[root], root))
    await agent.set_company('Example', 'https://company.example/', oid, 'Official company site')
    return run, agent, oid


async def test_full_archive_queue_years_pagination_attachments_and_dedup(tmp_path):
    root = 'https://company.example/newsroom'
    pages = {root: '<nav>' + ''.join(f'<a href="/nav/{i}">Menu {i}</a>' for i in range(100)) + '</nav>'
             '<a href="/newsroom/year/2024">2024</a><a href="/newsroom/year/2025">2025</a>'
             '<a href="/newsroom/year/2026">2026</a><h3><a href="/newsroom/year/2023">2023</a></h3>'
             '<h3><a href="/newsroom/year/-1">All</a></h3><a href="/newsroom/year/2026/print/1">Print</a>'}
    eligible = []
    for year, count in [(2024, 5), (2025, 40), (2026, 26)]:
        rows = ''
        for i in range(count):
            target = f'https://company.example/news/id/{year}-{i}'
            rows += f'<li><a href="{target}">Routine notice {year}-{i}</a> 1 November {year}</li>'
            pages[target] = f'<html><title>Notice</title><main><a href="/files/{year}-{i}.pdf">Attachment</a></main></html>'
            pages[f'https://company.example/files/{year}-{i}.pdf'] = b'%PDF-1.4\n' + target.encode()
            eligible.append(target)
        if year == 2024:
            rows += '<li><a href="/news/id/old">Old notice</a> 31 October 2024</li>'
        pages[f'{root}/year/{year}'] = '<ul>' + rows + '</ul><a href="/newsroom">Latest</a>'
    # Latest repeats entries from the annual archive. Pagination cycles must end.
    pages[root] += '<li><a href="/news/id/2026-0">Routine notice 2026-0</a> 1 November 2026</li><a rel="next" href="?page=2">Next</a>'
    pages[root + '?page=2'] = '<a rel="next" href="/newsroom">Next</a>'
    run, agent, _ = await agent_for(tmp_path, pages)
    await agent.register_archive(root, 'announcements')
    assert all(any(d['url'] == url and d['status'] == 'saved' for d in run.documents) for url in eligible)
    assert len([d for d in run.documents if (d.get('file') or '').endswith('.pdf')]) == 71
    assert len([d for d in run.documents if (d.get('file') or '').endswith('.html')]) == 71
    assert all(d['published_date'] for d in run.documents)
    assert len([d for d in run.documents if d['date_method'] == 'announcement_row']) == 71
    assert 'https://company.example/news/id/old' not in agent.web.calls
    assert not any('/2023' in u or '/nav/' in u for u in agent.web.calls)
    assert not any('/print/' in u or '/year/-1' in u for u in agent.web.calls)
    assert len(agent.web.calls) == len(set(agent.web.calls))
    assert run.queue_counts()['pending'] == 0 and run.queue_counts()['failed'] == 0
    await agent.update_archive(root, 'checked', 'All observed year pages and pagination processed')
    assert (await agent.finish('Downloaded source documents.', []))['status'] == 'finished'
    run.finalize()
    stored = json.loads((run.folder / 'catalogue.json').read_text())
    assert stored['queue_counts']['attachments'] == 71


async def test_undated_rows_and_selector_fallback(tmp_path):
    root = 'https://company.example/newsroom'
    pages = {root: '<div class="disclosures"><div class="entry"><a href="/item/abc">Notice without date</a></div></div><a class="more" href="?page=2">Continue</a>',
             root + '?page=2': '<div class="disclosures"><div class="entry"><a href="/item/def">Another undated notice</a></div></div>',
             'https://company.example/item/abc': '<html><p>Official text-only notice</p></html>',
             'https://company.example/item/def': '<html><p>Another official text-only notice</p></html>'}
    run, agent, oid = await agent_for(tmp_path, pages)
    await agent.scan_archive(oid, '.disclosures .entry', 'a.more')
    assert len(run.documents) == 2 and all(d['status'] == 'saved' for d in run.documents)
    assert run.documents[0]['date_status'] == 'needs_review'
    assert run.queue_counts()['completed'] == 4


async def test_new_source_upgrades_date_without_redownloading(tmp_path):
    root = 'https://company.example/newsroom'
    pages = {root: '<li><a href="/report.pdf">Report</a></li>',
             'https://company.example/report.pdf': b'%PDF-1.4\nReport'}
    run, agent, _ = await agent_for(tmp_path, pages)
    await agent.register_archive(root, 'reports')
    assert run.documents[0]['date_status'] == 'needs_review'
    annual = root + '/year/2026'
    agent.observe(annual, extract_html('<li><a href="/report.pdf">Report</a> 5 May 2026</li>', annual))
    await agent.register_archive(annual, 'reports')
    assert run.documents[0]['published_date'] == '2026-05-05'
    assert run.documents[0]['sources'] == [root, annual]
    assert agent.web.calls == ['https://company.example/report.pdf']
    assert not run.issues


async def test_unofficial_archive_cannot_download_documents(tmp_path):
    root = 'https://company.example/newsroom'
    pages = {root: '<html>Official homepage</html>',
             'https://unrelated.example/archive': '<a href="https://unrelated.example/file.pdf">Report</a>'}
    run, agent, _ = await agent_for(tmp_path, pages)
    agent.known.add('https://unrelated.example/archive')  # A search lead only.
    await agent.register_archive('https://unrelated.example/archive', 'reports')
    assert not run.documents
    assert run.queue_counts()['failed'] == 1
    assert not any(u.endswith('.pdf') for u in agent.web.calls)


async def test_published_tracker_target_preserves_provenance(tmp_path):
    root = 'https://company.example/newsroom'
    target = 'https://files.example/AR%20A.pdf?x=A%26B'
    from urllib.parse import quote
    tracker = 'https://tracking.example/tracker?redirect=' + quote(target, safe='')
    pages = {root: f'<li><a href="{tracker}">Annual report</a> 9 January 2025</li>', target: b'%PDF-1.4\nAnnual'}
    run, agent, _ = await agent_for(tmp_path, pages)
    await agent.register_archive(root, 'reports')
    assert agent.web.calls == [target]
    assert len(run.documents) == 1 and run.documents[0]['source_url'] == root
    assert run.documents[0]['published_date'] == '2025-01-09'
    assert next(x for x in agent.links.values() if x['url'] == target)['via_url'] == tracker


async def test_failures_do_not_skip_later_items_or_allow_false_checked(tmp_path):
    root = 'https://company.example/newsroom'
    pages = {root: '<li><a href="/missing.pdf">Missing</a> 1 May 2026</li><li><a href="/ok.pdf">Good</a> 1 May 2026</li>',
             'https://company.example/missing.pdf': ValueError('Not available'),
             'https://company.example/ok.pdf': b'%PDF-1.4\nGood'}
    run, agent, _ = await agent_for(tmp_path, pages)
    async def render(url):
        return url, pages[root], []
    agent.browser.open = render
    await agent.register_archive(root, 'announcements')
    assert run.queue_counts()['failed'] == 1
    assert any(d['status'] == 'saved' for d in run.documents)
    with pytest.raises(ValueError, match='unresolved'):
        await agent.update_archive(root, 'checked', 'All links found')
    assert (await agent.finish('Done', []))['status'] == 'continue'
    assert (await agent.finish('Done', []))['status'] == 'continue'
    await agent.update_archive(root, 'blocked', 'One published file is unavailable after retries')
    assert (await agent.finish('Partial', []))['status'] == 'partial'
    assert any('Queued document unresolved' in s for s in run.issues)


async def test_cancel_marks_unfinished_queue_and_preserves_saved(tmp_path):
    root = 'https://company.example/newsroom'
    pages = {root: '<li><a href="/one.pdf">One</a> 1 May 2026</li><li><a href="/two.pdf">Two</a> 1 May 2026</li>',
             'https://company.example/one.pdf': b'%PDF-1.4\nOne'}
    run, agent, _ = await agent_for(tmp_path, pages)
    original = agent.web.fetch
    entered = asyncio.Event()
    async def fetch(url, **kwargs):
        if url.endswith('/two.pdf'):
            entered.set()
            await asyncio.Event().wait()
        return await original(url, **kwargs)
    agent.web.fetch = fetch
    task = asyncio.create_task(agent.register_archive(root, 'announcements'))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    run.status = 'cancelled'
    run.finalize()
    assert run.documents[0]['status'] == 'saved'
    assert run.queue_counts()['interrupted'] == 1
    assert not agent.draining


def test_row_dates_do_not_confuse_reporting_periods_or_navigation():
    data = extract_html('<nav><a href="/home">Home</a></nav><ul><li><a href="/news/id/1">Results for year ended 30 September 2025</a> 28 November 2025</li></ul>', 'https://company.example/newsroom')
    assert link_date(data['links'][1]) == (date(2025, 11, 28), '28 November 2025')
    assert link_date(data['links'][0]) == (None, '')
    assert link_date({'label': 'Results for year ended 30 September 2025', 'context': 'Results for year ended 30 September 2025'}) == (None, '')
