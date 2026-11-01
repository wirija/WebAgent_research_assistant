import asyncio
import hashlib
import io
import json
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from company_understanding.documents import extract, load_collection, source_path
from company_understanding.engine import Job, Understanding
from company_understanding.models import Extraction, Request, ProfileItem, ProfileSection
from company_understanding.sample import SampleClient, create_sample


def make_job(tmp_path, **kwargs):
    folder = create_sample(tmp_path / 'input')
    request = Request(source_folder=str(folder), financial_year_end='2026-09-30', information_cutoff='2026-11-01')
    return Job(request, tmp_path / 'jobs', **kwargs)


async def test_independent_workflow_exports_evidence_and_leaves_input_unchanged(tmp_path):
    job = make_job(tmp_path, demo=True)
    original = (job.source / 'catalogue.json').read_bytes()
    result = await Understanding(job, client=SampleClient()).execute()
    assert result['status'] == 'finished'
    assert result['usage']['model_calls'] == 0 and result['usage']['simulated_calls'] > 0
    assert len(result['facts']) == 6
    assert result['profile'] and result['significant_changes'] and result['exposures'] and result['open_questions']
    assert all(x['review']['status'] == 'supported' for x in result['profile'])
    assert result['exposures'][0]['kind'] == 'inference'
    assert result['documents'][0]['status'] == 'processed'
    assert (job.source / 'catalogue.json').read_bytes() == original
    for filename in ('job.json', 'company_profile.json', 'significant_changes.json', 'exposure_register.json', 'open_questions.json', 'report.html', 'evidence/chunks.jsonl'):
        assert (job.folder / filename).is_file()
    assert 'SYNTHETIC SAMPLE' in (job.folder / 'report.html').read_text()
    assert 'API_KEY' not in (job.folder / 'job.json').read_text()
    assert 'webagent' not in sys.modules  # No collector import is needed.


async def test_cutoff_dedup_and_unknown_publication_are_explicit(tmp_path):
    job = make_job(tmp_path)
    source = job.manifest['documents'][0]
    job.manifest['documents'] = [source | {'published_date': None}, source | {'id': 'copy'},
                                 source | {'id': 'future', 'published_date': '2026-12-01'},
                                 {'id': 'missing', 'title': 'Missing', 'status': 'failed'}]
    result = await Understanding(job, client=SampleClient()).execute()
    assert [d['status'] for d in result['documents']] == ['processed', 'duplicate', 'excluded_after_cutoff', 'unavailable']
    assert result['status'] == 'partial'
    assert all(x['review']['status'] == 'needs_review' for x in result['profile'])
    assert any('cutoff' in x for x in result['issues'])


async def test_fabricated_citations_and_unquoted_numbers_do_not_become_findings(tmp_path):
    job = make_job(tmp_path)
    engine = Understanding(job, client=SampleClient())
    engine.fact_fingerprints = set()
    await engine.ingest()
    chunk = next(iter(engine.chunks.values()))
    good = {'area': 'financing', 'statement': 'Loans were SGD 200 million.', 'kind': 'documented_fact', 'period': '2026', 'entity_scope': 'Group',
            'citations': [{'chunk_id': chunk['id'], 'quote': 'The Group has bank loans of SGD 1 million at 30 September 2026.'}]}
    bad_id = good | {'citations': [{'chunk_id': 'invented', 'quote': 'Invented evidence'}]}
    bad_quote = good | {'citations': [{'chunk_id': chunk['id'], 'quote': 'Bank loans of SGD 200 million'}]}
    engine.accept_extraction(Extraction(facts=[good, bad_id, bad_quote], document_notes=[], warnings=[]), [chunk])
    assert not engine.fact_lookup
    assert job.data['progress']['citations_rejected'] == 2
    assert any('numerical' in x for x in job.data['issues'])


async def test_changes_are_found_even_when_original_fact_is_in_operations(tmp_path):
    job = make_job(tmp_path, demo=True)
    engine = Understanding(job, client=SampleClient())
    engine.fact_fingerprints = set()
    await engine.ingest()
    await engine.extraction_batch(list(engine.chunks.values()))
    event = next(x for x in engine.fact_lookup.values() if 'opened' in x['statement'])
    event['area'] = 'operations'
    await engine.build_profile(areas=['significant_changes'])
    assert len(job.data['significant_changes']) == 1
    assert '1 July 2026' in job.data['significant_changes'][0]['statement']


async def test_inferred_changes_become_questions_not_timeline_facts(tmp_path):
    class InferredChange(SampleClient):
        async def parse(self, text_format, input, **kwargs):
            result = await super().parse(text_format=text_format, input=input, **kwargs)
            if text_format is ProfileSection:
                result.output_parsed.items.append(ProfileItem(title='Possible outlet increase', statement='The opening may have increased the outlet count.',
                    kind='inference', period='unknown', entity_scope='unknown', fact_ids=['fact-1']))
            return result
    job = make_job(tmp_path, demo=True)
    engine = Understanding(job, client=InferredChange())
    engine.fact_fingerprints = set()
    await engine.ingest()
    await engine.extraction_batch(list(engine.chunks.values()))
    await engine.build_profile(areas=['significant_changes'])
    assert len(job.data['significant_changes']) == 1
    assert any('Possible outlet increase' in q['question'] for q in job.data['open_questions'])


def test_cli_invalid_input_has_unambiguous_exit_code():
    from company_understanding.cli import main
    with pytest.raises(SystemExit) as error:
        main(['run'])
    assert error.value.code == 64


async def test_source_hash_mismatch_and_escape_are_not_read(tmp_path):
    job = make_job(tmp_path)
    job.manifest['documents'][0]['sha256'] = 'incorrect'
    result = await Understanding(job, client=SampleClient()).execute()
    assert result['status'] == 'failed'
    assert result['documents'][0]['status'] == 'unreadable'
    outside = tmp_path / 'secret.txt'
    outside.write_text('private')
    with pytest.raises(ValueError, match='outside'):
        source_path(job.source, '../secret.txt')
    (job.source / 'linked.txt').symlink_to(outside)
    with pytest.raises(ValueError, match='outside'):
        source_path(job.source, 'linked.txt')


def test_all_pdf_pages_have_physical_page_locations(tmp_path):
    pdf = PdfWriter()
    for index in range(14):
        page = pdf.add_blank_page(width=600, height=800)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        stream.set_data(f'BT /F1 12 Tf 20 700 Td (Company disclosure on physical page {index+1} with relevant evidence.) Tj ET'.encode())
        page[NameObject('/Contents')] = pdf._add_object(stream)
    path = tmp_path / 'report.pdf'
    pdf.write(path)
    chunks, issues, count = extract(path, 'source-1')
    assert count == len(chunks) == 14
    assert not issues
    assert chunks[-1]['page'] == 14 and chunks[-1]['location'] == 'PDF page 14'
    assert 'physical page 14' in chunks[-1]['text']


def test_html_and_presentation_extraction_keep_source_locations(tmp_path):
    path = tmp_path / 'document.html'
    path.write_text('<nav>Ignore navigation</nav><main><h1>Actual announcement</h1><p>Business operations in Singapore.</p><script>bad code</script></main>')
    chunks, _, _ = extract(path, 'source-1')
    assert 'Ignore navigation' not in chunks[0]['text'] and 'bad code' not in chunks[0]['text']
    ppt = tmp_path / 'slides.pptx'
    with zipfile.ZipFile(ppt, 'w') as archive:
        archive.writestr('ppt/slides/slide2.xml', '<root xmlns:a="urn:example"><a:t>Second slide: costs and financing information.</a:t></root>')
        archive.writestr('ppt/slides/slide1.xml', '<root xmlns:a="urn:example"><a:t>First slide: our company business model.</a:t></root>')
    chunks, _, count = extract(ppt, 'source-2')
    assert count == 2 and chunks[0]['location'] == 'Slide 1'
    assert chunks[0]['text'].startswith('First slide')


async def test_cancel_and_refusal_preserve_intermediate_coverage(tmp_path):
    job = make_job(tmp_path)
    started = asyncio.Event()
    class Waiting(SampleClient):
        async def parse(self, **kwargs):
            started.set()
            await asyncio.Event().wait()
    task = asyncio.create_task(Understanding(job, client=Waiting()).execute())
    await started.wait()
    task.cancel()
    result = await task
    assert result['status'] == 'cancelled' and result['finished_at']
    assert result['documents'][0]['status'] == 'interrupted'
    assert (job.folder / 'report.html').exists()
    refused = make_job(tmp_path / 'refusal')
    class Refusal(SampleClient):
        async def parse(self, **kwargs):
            return SimpleNamespace(status='completed', output_parsed=None, usage=None)
    assert (await Understanding(refused, client=Refusal()).execute())['status'] == 'failed'


async def test_real_sdk_serializes_structured_schema_and_requested_model(tmp_path):
    captured = []
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={'id': 'resp_test', 'object': 'response', 'created_at': 1, 'model': 'gpt-5-mini', 'status': 'completed',
            'output': [{'id': 'msg_test', 'type': 'message', 'status': 'completed', 'role': 'assistant', 'content': [
                {'type': 'output_text', 'text': json.dumps({'facts': [], 'document_notes': [], 'warnings': []}), 'annotations': []}]}],
            'usage': {'input_tokens': 10, 'output_tokens': 10, 'total_tokens': 20}})
    client = AsyncOpenAI(api_key='sk-test-not-real', http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    engine = Understanding(make_job(tmp_path), client=client)
    result = await engine.ask(Extraction, 'Extract facts.', {'chunks': []})
    assert result.facts == []
    assert captured[0]['model'] == 'gpt-5-mini' and captured[0]['store'] is False
    assert captured[0]['text']['format']['type'] == 'json_schema'
    assert captured[0]['text']['format']['strict'] is True
    await client.close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    from company_understanding import server
    monkeypatch.setattr(server, 'OUTPUT', tmp_path / 'jobs')
    monkeypatch.setattr(server, 'JOBS', {})
    monkeypatch.setattr(server, 'TASKS', {})
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    with TestClient(server.app) as client:
        yield client


def test_standalone_api_demo_exports_and_security(client):
    assert client.post('/api/demo', json={}).status_code == 403
    headers = {'x-session-token': client.get('/api/config').json()['session_token']}
    assert client.post('/api/demo', json={}, headers=headers | {'origin': 'https://unrelated.example'}).status_code == 403
    response = client.post('/api/demo', json={}, headers=headers)
    assert response.status_code == 200
    job_id = response.json()['id']
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        data = client.get('/api/jobs/' + job_id).json()
        if data['status'] != 'running':
            break
        time.sleep(.05)
    assert data['status'] == 'finished' and data['demo']
    assert data['usage']['model_calls'] == 0
    report = client.get(f'/api/jobs/{job_id}/files/report.html')
    assert 'attachment' in report.headers['content-disposition']
    assert b'SYNTHETIC SAMPLE' in report.content
    archive = client.get(f'/api/jobs/{job_id}/zip')
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        assert 'exposure_register.json' in bundle.namelist()
        assert 'evidence/chunks.jsonl' in bundle.namelist()
    assert client.get(f'/api/jobs/{job_id}/files/.env').status_code == 404
    assert client.get(f'/api/jobs/{job_id}/sources/source-1').status_code == 200
    assert client.get('/api/jobs').json()[0]['id'] == job_id


def test_layout_extraction_preserves_html_table_headers_and_rows(tmp_path):
    file=tmp_path/'table.html'
    file.write_text('<main><h1>Employee costs — Group — SGD thousands</h1><table><tr><th>2025</th><th>2024</th></tr><tr><td>1,200</td><td>900</td></tr></table></main>')
    chunks,_,_=extract(file,'table')
    assert '2025 | 2024' in chunks[0]['text'] and '1,200 | 900' in chunks[0]['text']
    assert '\n' in chunks[0]['text'] and 'SGD thousands' in chunks[0]['text']


async def test_new_question_coverage_and_claim_outputs(tmp_path):
    job=make_job(tmp_path,demo=True)
    result=await Understanding(job,client=SampleClient()).execute()
    assert len(result['research_questions'])>=9
    assert len(result['question_coverage'])==len(result['research_questions'])
    assert all(x['status']=='not_established' for x in result['question_coverage'])
    assert result['claim_checks'] and all(x['status']=='supported' for x in result['claim_checks'])
    for name in ['research_questions.json','question_coverage.json','evidence_comparisons.json','claim_checks.json']:
        assert (job.folder/name).is_file()


async def test_claim_verification_cannot_pass_without_matching_quotes(tmp_path):
    from company_understanding.models import ClaimCheck
    class InventedQuote(SampleClient):
        async def parse(self,text_format,**kwargs):
            r=await super().parse(text_format=text_format,**kwargs)
            if text_format is ClaimCheck:r.output_parsed.citations[0].quote='The company operates exclusively in Singapore and nowhere else.'
            return r
    job=make_job(tmp_path,demo=True)
    result=await Understanding(job,client=InventedQuote()).execute()
    assert result['status']=='partial'
    assert all(c['status']=='needs_review' for c in result['claim_checks'])
    assert all(p['review']['status']=='needs_review' for p in result['profile'])


async def test_comparisons_distinguish_time_and_conflict(tmp_path):
    from company_understanding.models import Comparisons, Comparison
    class Compare(SampleClient):
        async def parse(self,text_format,**kwargs):
            if text_format is Comparisons:
                facts=json.loads(kwargs['input'])['data']
                return SimpleNamespace(status='completed',usage=None,output_parsed=Comparisons(comparisons=[
                    Comparison(kind='change_over_time',explanation='Outlet count changed between reporting dates.',fact_ids=[f['id'] for f in facts]),
                    Comparison(kind='conflict',explanation='Two incompatible counts for the same date need checking.',fact_ids=[f['id'] for f in facts])]))
            return await super().parse(text_format=text_format,**kwargs)
    job=make_job(tmp_path,demo=True);worker=Understanding(job,client=Compare());worker.fact_fingerprints=set()
    await worker.ingest();await worker.extraction_batch(list(worker.chunks.values()))
    original=next(iter(worker.fact_lookup.values()))
    second=dict(original,id='extra',statement='Other disclosed count.')
    worker.fact_lookup['extra']=second
    await worker.compare_evidence()
    assert {c['kind'] for c in job.data['evidence_comparisons']}=={'change_over_time','conflict'}
    assert any('Conflicting company evidence' in x for x in job.data['issues'])


def test_management_forecast_cannot_lose_attribution():
    from company_understanding.verification import attribution_gap
    quotes=['Management expects demand to remain stable.']
    assert attribution_gap('Demand will remain stable.',quotes)
    assert not attribution_gap('Management expects demand to remain stable.',quotes)
    assert attribution_gap('Demand will remain stable.',['Longer source paragraph.'],kind='management_statement')
    assert not attribution_gap('Employee costs were SGD 2 million.',['Employee costs were SGD 2 million.'])


async def test_question_coverage_searches_across_original_fact_areas(tmp_path):
    from company_understanding.models import CoverageAnswer
    class Capture(SampleClient):
        seen=[]
        async def parse(self,text_format,**kwargs):
            if text_format is CoverageAnswer:self.seen.append(json.loads(kwargs['input'])['data'])
            return await super().parse(text_format=text_format,**kwargs)
    client=Capture();job=make_job(tmp_path,demo=True);worker=Understanding(job,client=client);worker.fact_fingerprints=set()
    await worker.ingest();await worker.extraction_batch(list(worker.chunks.values()));await worker.plan_questions();await worker.question_coverage()
    operations=next(x for x in client.seen if x['question']['area']=='operations')
    assert any(f['area']=='business_model' and 'Singapore' in f['statement'] for f in operations['facts'])
