"""Public JUMBO archive/download regression; no OpenAI calls or credentials.

Uses the official linked IR source already established in the reported test.
Tests collection from that source, not company discovery. Temporary originals
are removed after verifying the manifest; a concise report is kept in docs/.
"""
import asyncio
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webagent.agent import Agent
from webagent.state import Run, RunRequest
from webagent.web import Web, extract_html


async def main():
    with tempfile.TemporaryDirectory(prefix='webagent-queue-check-') as temp:
        run = Run(RunRequest(company='JUMBO collection regression'), Path(temp))
        run.start_date, run.end_date = date(2024, 11, 1), date(2026, 11, 1)
        agent = Agent(run, 'unused-no-model-calls', web=Web())
        root = 'https://jumbogroup.listedcompany.com/newsroom.html'
        try:
            # This is a focused collector test with established source identity.
            run.identity = {'name': 'JUMBO Group', 'official_url': 'https://www.jumbogroup.sg/',
                            'evidence': 'Official linked IR archive from the reported JUMBO run'}
            fetched = await agent.web.fetch(root)
            agent.observe(fetched.url, extract_html(fetched.body, fetched.url), trusted=True)
            await agent.register_archive(root, 'announcements')
            announcements = [d for d in run.documents if '/news.html/id/' in d['url'] and d['status'] != 'excluded_date']
            report = {'test': 'Live collection from established official JUMBO archive; no model calls',
                      'window': [str(run.start_date), str(run.end_date)],
                      'announcements': len(announcements), 'counts': run.counts(),
                      'queue_counts': run.queue_counts(), 'problems': agent.queue_problems(),
                      'announcement_dates_missing': sum(not d['published_date'] for d in announcements),
                      'archive_scans': run.archives[root].get('scans', []),
                      'pdf_files': sum((d.get('file') or '').endswith('.pdf') and d['status'] == 'saved' for d in run.documents)}
            destination = Path(__file__).resolve().parents[1] / 'docs' / 'jumbo-queue-validation.json'
            destination.write_text(json.dumps(report, indent=2))
            print(json.dumps(report, indent=2), flush=True)
            assert len(announcements) >= 71, 'Some previously audited announcement rows were missed'
            assert not report['announcement_dates_missing'], 'Announcement row dates were not recognized'
            assert report['pdf_files'] > 0, 'No announcement attachments were downloaded'
            assert not report['queue_counts']['pending'], 'Queue was not fully processed'
            assert not report['queue_counts']['failed'], 'Downloads or archive pages remain failed'
        finally:
            await agent.browser.close()
            await agent.web.close()
            await agent.client.close()


if __name__ == '__main__':
    asyncio.run(main())
