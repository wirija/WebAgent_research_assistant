"""Deterministic synthetic demo exercising the real extraction/review workflow."""
import json
from pathlib import Path
from types import SimpleNamespace

from .models import Extraction, ExposurePlan, ProfileSection, Review


TEXT = 'Example Eats operates three restaurants in Singapore. The restaurants operate from leased premises. Employee expenses were SGD 2 million for the year ended 30 September 2026. The Group opened one restaurant on 1 July 2026. Management expects demand to remain stable. The Group has bank loans of SGD 1 million at 30 September 2026.'


def create_sample(folder):
    folder = Path(folder)
    (folder / 'documents').mkdir(parents=True, exist_ok=True)
    (folder / 'documents' / 'sample.html').write_text('<html><title>Synthetic Example Eats report</title><main>' + TEXT + '</main></html>')
    # Input is allowed only via the explicit demo endpoint. The resulting Job
    # and every exported artifact carry demo=true.
    (folder / 'catalogue.json').write_text(json.dumps({'company': 'Example Eats — synthetic sample', 'status': 'finished',
        'documents': [{'id': 'demo-doc', 'title': 'Synthetic annual report', 'category': 'annual_report',
                       'file': 'documents/sample.html', 'published_date': '2026-10-15', 'status': 'saved'}]}))
    return folder


from .models import QuestionPlan, CoverageAnswer, Comparisons, ClaimPlan, ClaimCheck


class SampleClient:
    def __init__(self):
        self.responses = self

    async def parse(self, text_format, input, **kwargs):
        data = json.loads(input)['data']
        if text_format is Extraction:
            chunks = data['chunks']
            definitions = [('business_model', 'Example Eats operates three restaurants in Singapore.'),
                           ('costs_suppliers', 'Employee expenses were SGD 2 million for the year ended 30 September 2026.'),
                           ('assets', 'The restaurants operate from leased premises.'),
                           ('financing', 'The Group has bank loans of SGD 1 million at 30 September 2026.'),
                           ('significant_changes', 'The Group opened one restaurant on 1 July 2026.'),
                           ('management_outlook', 'Management expects demand to remain stable.')]
            facts = [{'area': area, 'statement': quote, 'kind': 'management_statement' if area == 'management_outlook' else 'documented_fact',
                      'period': 'Year ended 30 September 2026' if area != 'management_outlook' else 'Forward looking', 'entity_scope': 'Group',
                      'citations': [{'chunk_id': chunks[0]['id'], 'quote': quote}]} for area, quote in definitions]
            result = {'facts': facts, 'document_notes': [], 'warnings': []}
        elif text_format is ExposurePlan:
            fact = next((f for f in data if 'leased' in f['statement']), data[0])
            result = {'exposures': [{'title': 'Premises and lease commitments',
                       'explanation': 'The Group uses leased premises. Renewal terms and outlet profitability need clarification before assessing the extent of exposure.',
                       'fact_ids': [fact['id']], 'uncertainties': ['Lease renewal dates are not established from this sample.'],
                       'questions': ['When do the restaurant leases expire, and what are the renewal terms?'], 'search_terms': ['leased', 'lease', 'renewal']}]}
        elif text_format is ProfileSection:
            if 'Build the significant_changes part' in kwargs.get('instructions', ''):
                data = [f for f in data if 'opened' in f['statement']]
            result = {'items': [{'title': f['area'].replace('_', ' ').title(), 'statement': f['statement'], 'kind': f['kind'],
                                 'period': f['period'], 'entity_scope': f['entity_scope'], 'fact_ids': [f['id']]} for f in data]}
        elif text_format is QuestionPlan:
            result={'questions':[]}
        elif text_format is CoverageAnswer:
            result={'status':'not_established','answer':'The synthetic excerpt only partly answers this question.',
                    'fact_ids':[f['id'] for f in data['facts']], 'missing_information':['Full disclosures are not available in this sample.']}
        elif text_format is Comparisons:
            result={'comparisons':[]}
        elif text_format is ClaimPlan:
            result={'claims':[{'statement':data.get('statement') or data['explanation'], 'question':'What does the source disclose?', 'search_terms':[]}]}
        elif text_format is ClaimCheck:
            result={'status':'supported','answer':'Synthetic sample: evidence is supplied.',
                    'citations':[{'chunk_id':data['chunks'][0]['id'], 'quote':data['chunks'][0]['text']}]}
        elif text_format is Review:
            result = {'decisions': [{'item_id': x['item']['id'], 'status': 'supported', 'reason': 'Synthetic sample: the supplied quote supports this demonstration item.'} for x in data]}
        else:
            raise ValueError('Unknown sample schema')
        return SimpleNamespace(output_parsed=text_format.model_validate(result), status='completed', usage=None)

    async def close(self):
        pass
