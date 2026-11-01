"""Small paid integration check: one topic, one cited public source, fictional company.
Not an exhaustive research or factual accuracy evaluation. Run from the project root.
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'modules'/'external_research'))
from dotenv import load_dotenv
import os
from external_research.engine import Job, Research, export, now
from external_research.models import Request
from external_research.sample import create_sample

async def main():
    load_dotenv(Path(__file__).resolve().parents[1]/'.env')
    root=Path(tempfile.mkdtemp(prefix='external-research-live-'))
    source=create_sample(root/'input');path=source/'job.json';data=json.loads(path.read_text())
    data['demo']=False;data['company']='Fictional restaurant — integration test only'
    data['profile'][0]['statement']='The fictional company operates restaurants in Singapore.'
    data['profile'][0]['citations'][0]['quote']=data['profile'][0]['statement']
    path.write_text(json.dumps(data))
    job=Job(Request(source_folder=str(source),audit_period_start='2025-01-01',audit_period_end='2025-12-31',information_cutoff='2026-11-01'),root/'output')
    worker=Research(job,os.getenv('OPENAI_API_KEY',''))
    topic={'id':'topic-1','title':'Singapore restaurant consumer prices','question':'What does official evidence say about restaurant price changes during 2025?',
        'relevance':'The fictional company operates restaurants in Singapore.','company_evidence_ids':['company-1'], 'geography':'Singapore',
        'search_query':'site.singstat.gov.sg Singapore 2025 consumer price index restaurant food serving services press release',
        'primary_sources':['Singapore Department of Statistics'],'status':'researching'}
    job.data['topics']=[topic]
    try:
        sources=await worker.search(topic)
        print('Provider-cited URLs:',len(sources),flush=True)
        selected=None
        for source in sources:
            try:
                captured=await worker.fetcher.fetch(source['url'])
                if sum(len(p['text']) for p in captured['parts'])>40000:continue
                worker.cache[source['url']]=captured;selected=source;break
            except Exception as exc:print('Source unavailable:',type(exc).__name__,flush=True)
        if not selected:raise ValueError('No suitably sized readable source for this focused integration check.')
        await worker.collect(topic,selected)
        await worker.synthesise(topic)
        await worker.review()
        job.data['status']='partial' if job.data['issues'] else 'finished'
        job.data.update(stage='Finished',finished_at=now());export(job)
        print(json.dumps({'output_folder':str(job.folder),'status':job.data['status'],'source':selected['url'],
            'observations':len(job.data['observations']),'implications':len(job.data['implications']),'usage':job.data['usage'],'issues':job.data['issues']},indent=2))
        if not job.data['observations']:raise SystemExit(1)
    finally:await worker.client.close()

if __name__=='__main__':asyncio.run(main())
