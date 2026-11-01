"""Opt-in smoke check: one paid model call and two public downloads.

Run from the project root: .venv/bin/python scripts/live_link_smoke.py
Uses captured public Singtel evidence, not a full discovery/coverage test.
Files are temporary; existing research output is untouched.
"""
import asyncio
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from webagent.agent import Agent, SYSTEM, TOOLS, safe_error
from webagent.state import Run, RunRequest


async def main():
    load_dotenv(ROOT / '.env')
    key = os.getenv('OPENAI_API_KEY')
    if not key:
        raise ValueError('No API key configured; live smoke check skipped.')
    with tempfile.TemporaryDirectory(prefix='webagent-live-check-') as folder:
        run = Run(RunRequest(company='Singtel'), Path(folder), os.getenv('OPENAI_MODEL', 'gpt-5-mini'))
        run.start_date, run.end_date = date(2024, 11, 1), date(2026, 11, 1)
        agent = Agent(run, key)
        try:
            data = json.loads((ROOT / 'tests/fixtures/singtel_links.json').read_text())
            oid = agent.observe(data.pop('url'), data)
            await agent.set_company('Singtel', 'https://www.singtel.com/', oid, 'Captured official source fixture')
            links = [x for x in agent.observations[oid]['links'] if any(t in x['context'] for t in ['Sustainability Report 2026', 'Gulf and Singtel form'])]
            response = await agent.client.responses.create(
                model=run.model, instructions=SYSTEM, input='Smoke check only: collect exactly these two documents using their link IDs. Identity is confirmed. Publication window 2024-11-01 through 2026-11-01. Captured links: ' + json.dumps(links),
                tools=[t for t in TOOLS if t['name'] == 'collect_documents'],
                tool_choice={'type': 'function', 'name': 'collect_documents'},
                parallel_tool_calls=False, reasoning={'effort': 'low'}, max_output_tokens=2000, store=False)
            calls = [x for x in response.output if x.type == 'function_call']
            if len(calls) != 1:
                raise ValueError('Expected one collection tool call.')
            args = json.loads(calls[0].arguments)
            if {x['link_id'] for x in args['documents']} != {x['link_id'] for x in links}:
                raise ValueError('Model did not select both expected link IDs.')
            print('GPT-5-mini selected both exact link IDs.', flush=True)
            result = await agent.collect(args['documents'])
            for item in result['results']:
                print(json.dumps({k:item.get(k) for k in ['title','status','published_date','date_method','bytes','error']}), flush=True)
            if any(x.get('status') != 'saved' for x in result['results']):
                raise ValueError('A live download did not succeed; see results above.')
            print('Live link-selection/download smoke check passed.', flush=True)
        finally:
            await agent.browser.close()
            await agent.web.close()
            await agent.client.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as exc:
        print(safe_error(exc), file=sys.stderr)
        sys.exit(1)
