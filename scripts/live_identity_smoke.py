"""Three opt-in paid model calls against synthetic identity-resolution cases.

These test reasoning at the post-search checkpoint; they are not web research
and do not assert that the synthetic businesses exist.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from openai import AsyncOpenAI
from webagent.agent import SYSTEM, TOOLS, safe_error

CASES = [
    ("Jumbo Singapore", [
        {"observation_id":"page-1", "url":"https://sg-jumbo.example/", "title":"JUMBO Group Limited", "facts":"Singapore seafood restaurant group; registered office Singapore; Singapore stock-exchange listing."},
        {"observation_id":"page-2", "url":"https://eu-jumbo.example/", "title":"Jumbo Retail", "facts":"European supermarket group; headquarters Netherlands; no Singapore company identity."},
    ], "set_company", "https://sg-jumbo.example/"),
    ("Singapore Noodles", [
        {"observation_id":"page-1", "url":"https://noodles-my.example/", "title":"Singapore Noodles Sdn Bhd", "facts":"Exact business name Singapore Noodles Sdn Bhd; food manufacturer; registered address and headquarters Kuala Lumpur, Malaysia. Singapore is part of its registered name."},
        {"observation_id":"page-2", "url":"https://food-sg.example/", "title":"Lion City Food Holdings", "facts":"Singapore-based food company; sells a dish called Singapore noodles, but this is not its company name."},
    ], "set_company", "https://noodles-my.example/"),
    ("Jumbo", [
        {"observation_id":"page-1", "url":"https://sg-jumbo.example/", "title":"JUMBO Group Limited", "facts":"Singapore seafood restaurant group; registered office Singapore; Singapore stock-exchange listing."},
        {"observation_id":"page-2", "url":"https://eu-jumbo.example/", "title":"Jumbo Retail", "facts":"European supermarket group commonly called Jumbo; registered office Netherlands."},
    ], "ask_user", None),
]


async def check(client, phrase, observations, expected, url):
    response = await client.responses.create(
        model=os.getenv('OPENAI_MODEL', 'gpt-5-mini'), instructions=SYSTEM,
        input='Synthetic identity-resolution test. Prior web search and official-page inspections have already produced the observations below. At this checkpoint select the company and proceed if evidence supports one match; otherwise ask a focused identity question. User input: ' + phrase + '\nOfficial observations: ' + json.dumps(observations),
        tools=[t for t in TOOLS if t['name'] in {'set_company','ask_user'}], tool_choice='required',
        parallel_tool_calls=False, reasoning={'effort':'low'}, max_output_tokens=1500, store=False)
    calls = [x for x in response.output if x.type == 'function_call']
    if len(calls) != 1 or calls[0].name != expected:
        raise ValueError(f'Identity check failed for {phrase}: expected {expected}.')
    args = json.loads(calls[0].arguments)
    if url and args.get('official_url') != url:
        raise ValueError(f'Wrong identity chosen for {phrase}.')
    print(f'{phrase}: {expected}' + (f' → {url}' if url else '') + ' — passed', flush=True)


async def main():
    load_dotenv(ROOT / '.env')
    async with AsyncOpenAI(api_key=os.getenv('OPENAI_API_KEY'), timeout=90) as client:
        await asyncio.gather(*(check(client, *case) for case in CASES))


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as exc:
        print(safe_error(exc), file=sys.stderr)
        sys.exit(1)
