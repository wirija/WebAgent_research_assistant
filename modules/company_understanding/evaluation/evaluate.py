"""Opt-in paid evidence-review evaluation; synthetic hand-labelled cases, not audit validation.
Run from the module directory: python evaluation/evaluate.py --env-file ../../.env
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import os
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from openai import AsyncOpenAI
from company_understanding.models import Review
from company_understanding.verification import REVIEW_INSTRUCTION, attribution_gap

async def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env-file',type=Path,default=Path('.env'))
    p.add_argument('--output',type=Path,default=Path('evaluation/latest-results.json'))
    args=p.parse_args();load_dotenv(args.env_file)
    cases=json.loads(Path(__file__).with_name('cases.json').read_text())
    async with AsyncOpenAI(timeout=120,max_retries=2) as client:
        result=await client.responses.parse(model=os.getenv('OPENAI_MODEL','gpt-5-mini'),store=False,reasoning={'effort':'low'},
            instructions=REVIEW_INSTRUCTION,
            input=json.dumps([{'item_id':c['id'],'claim':c['claim'],'quotes':c['quotes']} for c in cases]),text_format=Review)
    if result.output_parsed is None:raise ValueError('No structured review returned.')
    predictions={x.item_id:x for x in result.output_parsed.decisions}
    rows=[]
    for c in cases:
        model_status=predictions[c['id']].status if c['id'] in predictions else 'missing'
        predicted='needs_review' if attribution_gap(c['claim'],c['quotes']) else model_status
        rows.append(dict(c,model_predicted=model_status,predicted=predicted,passed=predicted==c['expected']))
    output={'scope':'Synthetic evidence-review regression only; not end-to-end company-understanding accuracy.','model':os.getenv('OPENAI_MODEL','gpt-5-mini'), 'passed':sum(r['passed'] for r in rows),'total':len(rows),'cases':rows}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(output,indent=2))
    print(f"{output['passed']}/{output['total']} evidence-review cases passed. Results: {args.output}")
    if output['passed']!=output['total']:raise SystemExit(1)

if __name__=='__main__':asyncio.run(main())
