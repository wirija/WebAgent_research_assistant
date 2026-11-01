import asyncio
import io
import json
import socket
import time
from types import SimpleNamespace
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from external_research.engine import Job, Research, load_input, validate_observation, observed_sources
from external_research.models import Request, Observation, Observations, Plan
from external_research.sample import SampleClient, SampleFetcher, create_sample, TEXT
from external_research.web import public_url, parse_content


def make_job(tmp_path,demo=True):
    source=create_sample(tmp_path/'input')
    return Job(Request(source_folder=str(source),audit_period_start='2025-10-01',audit_period_end='2026-09-30',information_cutoff='2026-11-01'),tmp_path/'output',demo=demo)

async def test_offline_workflow_and_exports(tmp_path):
    job=make_job(tmp_path)
    original=(Path(job.data['source_folder'])/'job.json').read_bytes()
    result=await Research(job,client=SampleClient(),fetcher=SampleFetcher()).execute()
    assert result['status']=='finished'
    assert len(result['topics'])==len(result['observations'])==len(result['implications'])==1
    assert result['implications'][0]['review']['status']=='supported'
    assert result['usage']['model_calls']==0 and result['usage']['simulated_calls']>0
    assert result['observations'][0]['publication_date']=='2026-10-15'
    for name in ['topics.json','company_evidence.json','sources.json','observations.json','implications.json','conflicts.json','gaps.json','report.html','job.json']:
        assert (job.folder/name).is_file()
    assert b'SYNTHETIC SAMPLE' in (job.folder/'report.html').read_bytes()
    assert (Path(job.data['source_folder'])/'job.json').read_bytes()==original
    assert (job.folder/result['sources'][0]['file']).exists()


def test_contract_rejects_demo_running_wrong_module_and_invalid_dates(tmp_path):
    source=create_sample(tmp_path/'input')
    with pytest.raises(ValueError,match='Synthetic'):load_input(source)
    path=source/'job.json';data=json.loads(path.read_text());data['demo']=False;data['status']='running';path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='finish'):load_input(source)
    path.write_text('[]')
    with pytest.raises(ValueError,match='object'):load_input(source)
    data['status']='finished';data['module']='webagent';path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='contract'):load_input(source)
    with pytest.raises(ValueError):Request(source_folder=str(source),audit_period_start='2026-10-01',audit_period_end='2026-09-30',information_cutoff='2026-11-01')


def observation(**kw):
    return Observation(statement='Sector wages increased by 4%.',quote='Sector wages increased by 4%.',measurement_period='2026',geography='Exampleland',units='percent',publication_date='2026-10-15',publication_date_quote='15 October 2026',source_type='government',publisher='Example Office',limitations=[],**kw)


def test_quotes_numbers_and_cutoff():
    raw=observation();text='Published 15 October 2026. Sector wages increased by 4%.'
    row,error=validate_observation(raw,text,'2026-09-30');assert not error and row['availability']=='after_cutoff'
    raw.publication_date_quote='not on page';row,_=validate_observation(raw,text,'2026-11-01');assert row['publication_date'] is None and row['availability']=='unverified'
    raw.statement='Sector wages increased by 40%.';assert validate_observation(raw,text,'2026-11-01')[0] is None
    raw.quote='fabricated source quotation';assert validate_observation(raw,text,'2026-11-01')[0] is None


async def test_future_unknown_failed_sources_and_upstream_review(tmp_path):
    job=make_job(tmp_path);job.data['information_cutoff']='2026-10-01'
    result=await Research(job,client=SampleClient(),fetcher=SampleFetcher()).execute()
    assert result['status']=='partial' and not result['observations'] and result['sources'][0]['excluded_observations']
    class Unknown(SampleClient):
        async def parse(self,text_format,**kwargs):
            r=await super().parse(text_format=text_format,**kwargs)
            if text_format is Observations:r.output_parsed.observations[0].publication_date=None
            return r
    job=make_job(tmp_path/'unknown');result=await Research(job,client=Unknown(),fetcher=SampleFetcher()).execute()
    assert result['status']=='partial' and result['implications'][0]['review']['status']=='needs_review'
    class Failed:
        async def fetch(self,url):raise ValueError('Unavailable source')
    job=make_job(tmp_path/'failed');result=await Research(job,client=SampleClient(),fetcher=Failed()).execute()
    assert result['sources'][0]['status']=='failed' and result['status']=='partial'
    job=make_job(tmp_path/'upstream');job.data['company_evidence'][0]['review']['status']='needs_review'
    result=await Research(job,client=SampleClient(),fetcher=SampleFetcher()).execute()
    assert result['implications'][0]['review']['status']=='needs_review'


async def test_unobserved_prose_urls_and_private_network_are_rejected(monkeypatch):
    response=SimpleNamespace(output=[{'type':'message','content':[{'type':'output_text','text':'https://invented.example','annotations':[]}]}])
    assert observed_sources(response)==[]
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError,match='Private'):await public_url('https://example.org/')
    with pytest.raises(ValueError):await public_url('file:///etc/passwd')
    with pytest.raises(ValueError):await public_url('https://user:pass@example.org')
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('1.1.1.1',443))])
    target,host,sni=await public_url('https://example.org/news?q=1')
    assert target=='https://1.1.1.1/news?q=1' and host==sni=='example.org'


async def test_cancel_preserves_evidence_and_terminal_status(tmp_path):
    started=asyncio.Event()
    class Waiting(SampleFetcher):
        async def fetch(self,url):started.set();await asyncio.Event().wait()
    job=make_job(tmp_path);task=asyncio.create_task(Research(job,client=SampleClient(),fetcher=Waiting()).execute())
    await started.wait();task.cancel();result=await task
    assert result['status']=='cancelled' and result['finished_at']
    assert result['sources'][0]['status']=='interrupted' and result['topics'][0]['status']=='interrupted'
    assert (job.folder/'report.html').exists()


async def test_actual_sdk_web_search_contract(tmp_path):
    captured=[]
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'id':'resp_test','object':'response','created_at':1,'model':'gpt-5-mini','status':'completed','output':[
            {'id':'msg_test','type':'message','status':'completed','role':'assistant','content':[{'type':'output_text','text':'Evidence.','annotations':[{'type':'url_citation','url':'https://example.org/source','title':'Source','start_index':0,'end_index':8}]}]}],
            'usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20}})
    sdk=AsyncOpenAI(api_key='sk-test-not-real',http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    engine=Research(make_job(tmp_path),client=sdk,fetcher=SampleFetcher())
    result=await engine.search({'id':'topic-1','search_query':'food service wages','primary_sources':['Statistics Office']})
    assert result[0]['url']=='https://example.org/source'
    assert captured[0]['tools']==[{'type':'web_search'}] and captured[0]['model']=='gpt-5-mini' and captured[0]['store'] is False
    await sdk.close()


def test_html_table_rows_and_scripts():
    parts,_=parse_content(b'<main><h1>Wages</h1><table><tr><th>Year</th><th>Percent</th></tr><tr><td>2025</td><td>4%</td></tr></table><script>bad</script></main>','text/html')
    assert 'Year | Percent' in parts[0]['text'] and '2025 | 4%' in parts[0]['text'] and 'bad' not in parts[0]['text']


def test_api_demo_security_exports(tmp_path,monkeypatch):
    from external_research import server
    monkeypatch.setattr(server,'OUTPUT',tmp_path);monkeypatch.setattr(server,'JOBS',{});monkeypatch.setattr(server,'TASKS',{})
    with TestClient(server.app) as client:
        h={'x-session-token':client.get('/api/config').json()['session_token']}
        assert client.post('/api/demo',json={}).status_code==403
        assert client.post('/api/demo',json={},headers=h|{'origin':'https://evil.example'}).status_code==403
        r=client.post('/api/demo',json={},headers=h);assert r.status_code==200
        job_id=r.json()['id']
        for _ in range(80):
            d=client.get('/api/jobs/'+job_id).json()
            if d['status']!='running':break
            time.sleep(.05)
        assert d['status']=='finished'
        assert client.get(f'/api/jobs/{job_id}/files/observations.json').status_code==200
        assert client.get(f'/api/jobs/{job_id}/files/.env').status_code==404
        source=client.get(f'/api/jobs/{job_id}/sources/source-1')
        assert source.status_code==200 and 'attachment' in source.headers['content-disposition']
        assert client.get(f'/api/jobs/{job_id}/zip').status_code==200


def test_evidence_ids_are_not_treated_as_financial_numbers():
    from external_research.engine import numbers
    assert numbers('CPI rose 0.9% in 2025 (observation-2; company-1).')=={'0.9%','2025'}
    assert numbers('Operating expenses rose 2%.')=={'2%'}


def test_module2_later_cutoff_cannot_leak_into_earlier_external_research(tmp_path):
    source=create_sample(tmp_path/'input');path=source/'job.json';data=json.loads(path.read_text())
    data['information_cutoff']='2026-11-01';path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='later information cutoff'):
        Job(Request(source_folder=str(source),audit_period_start='2025-10-01',audit_period_end='2026-09-30',information_cutoff='2026-10-01'),tmp_path/'output',demo=True)
