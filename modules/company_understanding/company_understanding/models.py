from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

Area = Literal['business_model', 'operations', 'customers', 'costs_suppliers',
               'assets', 'financing', 'accounting', 'management_outlook', 'significant_changes']
AREAS = list(Area.__args__)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Request(StrictModel):
    source_folder: str = Field(min_length=1, max_length=2000)
    financial_year_end: date
    information_cutoff: date
    api_key: SecretStr | None = None


class Citation(StrictModel):
    chunk_id: str
    quote: str


class Fact(StrictModel):
    area: Area
    statement: str
    kind: Literal['documented_fact', 'management_statement']
    period: str
    entity_scope: str
    citations: list[Citation]


class DocumentNote(StrictModel):
    document_id: str
    document_type: Literal['annual_report', 'interim_results', 'announcement', 'presentation', 'other']
    reporting_period: str
    entity_scope: str
    citations: list[Citation]


class Extraction(StrictModel):
    facts: list[Fact]
    document_notes: list[DocumentNote]
    warnings: list[str]


class ProfileItem(StrictModel):
    title: str
    statement: str
    kind: Literal['documented_fact', 'management_statement', 'inference']
    period: str
    entity_scope: str
    fact_ids: list[str]


class ProfileSection(StrictModel):
    items: list[ProfileItem]


class Exposure(StrictModel):
    title: str
    explanation: str
    fact_ids: list[str]
    uncertainties: list[str]
    questions: list[str]
    search_terms: list[str]


class ExposurePlan(StrictModel):
    exposures: list[Exposure]


class ReviewDecision(StrictModel):
    item_id: str
    status: Literal['supported', 'needs_review']
    reason: str


class Review(StrictModel):
    decisions: list[ReviewDecision]


class ResearchQuestion(StrictModel):
    area: Area
    question: str
    reason: str
    fact_ids: list[str]
    search_terms: list[str]


class QuestionPlan(StrictModel):
    questions: list[ResearchQuestion]


class CoverageAnswer(StrictModel):
    status: Literal['answered', 'conflicting_evidence', 'not_established']
    answer: str
    fact_ids: list[str]
    missing_information: list[str]


class Comparison(StrictModel):
    kind: Literal['conflict', 'change_over_time', 'different_scope']
    explanation: str
    fact_ids: list[str]


class Comparisons(StrictModel):
    comparisons: list[Comparison]


class Claim(StrictModel):
    statement: str
    question: str
    search_terms: list[str]


class ClaimPlan(StrictModel):
    claims: list[Claim]


class ClaimCheck(StrictModel):
    status: Literal['supported', 'unsupported', 'conflicting_evidence', 'not_established']
    answer: str
    citations: list[Citation]
