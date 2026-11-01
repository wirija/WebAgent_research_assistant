"""Explicitly synthetic, offline demonstration: no search or API usage."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from .models import Plan, Observations, Synthesis, Review

TEXT='Published 15 October 2026. Exampleland food-service wages increased by 4% in the year ended September 2026. This is a synthetic statistic for demonstration only.'


def create_sample(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    (folder/'job.json').write_text(json.dumps({'contract_version':'1.0','module':'company_understanding','status':'finished',
        'id':'sample-module2','company':'Example Eats — synthetic sample','demo':True,
        'profile':[{'id':'profile-1','statement':'Example Eats operates restaurants in Exampleland and employs restaurant staff.',
            'period':'2026','entity_scope':'Group','review':{'status':'supported','reason':'Synthetic fixture'},
            'citations':[{'quote':'Example Eats operates restaurants in Exampleland and employs restaurant staff.','source_file':'synthetic-company.txt','location':'Sample paragraph'}]}],
        'exposures':[],'significant_changes':[]}))
    return folder


class SampleFetcher:
    async def fetch(self,url):
        body=('<html><main>'+TEXT+'</main></html>').encode()
        return {'url':url,'parts':[{'location':'Synthetic HTML page','text':TEXT}],'date_metadata':[],
                'sha256':hashlib.sha256(body).hexdigest(),'body':body,'extension':'.html'}


class SampleClient:
    def __init__(self):self.responses=self
    async def create(self,**kwargs):
        result={'type':'message','content':[{'type':'output_text','annotations':[{'type':'url_citation','url':'https://example.org/synthetic-wages','title':'Synthetic wage publication'}]}]}
        return SimpleNamespace(status='completed',usage=None,output=[result],output_text='Synthetic search result; no web search performed.')
    async def parse(self,text_format,input,**kwargs):
        data=json.loads(input)['data']
        if text_format is Plan:
            result={'topics':[{'title':'Restaurant labour costs','question':'How are food-service wages changing?',
                'relevance':'The company employs restaurant staff.','company_evidence_ids':[data[0]['id']],
                'geography':'Exampleland','search_query':'Exampleland food service wage statistics 2026', 'primary_sources':['Synthetic Statistics Office']}]}
        elif text_format is Observations:
            result={'observations':[{'statement':'Exampleland food-service wages increased by 4% in the year ended September 2026.',
                'quote':'Exampleland food-service wages increased by 4% in the year ended September 2026.',
                'measurement_period':'Year ended September 2026','geography':'Exampleland','units':'Percent annual change',
                'publication_date':'2026-10-15','publication_date_quote':'15 October 2026', 'source_type':'government',
                'publisher':'Synthetic Statistics Office','limitations':['Synthetic demonstration; not actual economic data.']} ]}
        elif text_format is Synthesis:
            result={'implications':[{'explanation':'Sector wage growth could increase staffing costs. The company’s own wage agreements and staffing mix are not established.',
                'observation_ids':[data['observations'][0]['id']], 'company_evidence_ids':[data['company_evidence'][0]['id']],
                'uncertainties':['Company wage agreements are unknown.'],'follow_up_questions':['How does the company’s staffing mix compare with the sector?']}],
                'conflicts':[],'gaps':['Company-specific wage sensitivity has not been established.']}
        elif text_format is Review:
            result={'decisions':[{'item_id':data['item']['id'],'status':'supported','reason':'Synthetic example links the conditional implication to both evidence sets.'}]}
        else:raise ValueError('Unknown sample schema')
        return SimpleNamespace(status='completed',output_parsed=text_format.model_validate(result),usage=None)
    async def close(self):pass
