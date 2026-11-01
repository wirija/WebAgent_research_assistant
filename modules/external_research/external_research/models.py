from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')

class Request(Strict):
    source_folder: str = Field(min_length=1, max_length=2000)
    audit_period_start: date
    audit_period_end: date
    information_cutoff: date
    api_key: SecretStr | None = None

    @model_validator(mode='after')
    def dates(self):
        if self.audit_period_start>self.audit_period_end:
            raise ValueError('Audit period start must be on or before its end.')
        return self

class Topic(Strict):
    title: str
    question: str
    relevance: str
    company_evidence_ids: list[str]
    geography: str
    search_query: str
    primary_sources: list[str]

class Plan(Strict):
    topics: list[Topic]

class Observation(Strict):
    statement: str
    quote: str
    measurement_period: str
    geography: str
    units: str
    publication_date: str | None
    publication_date_quote: str
    source_type: Literal['government','central_bank','regulator','industry_body','research','news','other']
    publisher: str
    limitations: list[str]

class Observations(Strict):
    observations: list[Observation]

class Implication(Strict):
    explanation: str
    observation_ids: list[str]
    company_evidence_ids: list[str]
    uncertainties: list[str]
    follow_up_questions: list[str]

class Synthesis(Strict):
    implications: list[Implication]
    conflicts: list[str]
    gaps: list[str]

class Verdict(Strict):
    item_id: str
    status: Literal['supported','needs_review']
    reason: str

class Review(Strict):
    decisions: list[Verdict]
