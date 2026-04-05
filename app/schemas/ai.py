import uuid

from pydantic import BaseModel


class InferredKnowledgeItem(BaseModel):
    knowledge_type: str
    title: str
    content_raw: str
    confidence: float = 0.0


class InferKnowledgeRequest(BaseModel):
    offer_id: uuid.UUID
    language: str = "zh-CN"
    user_hint: str | None = None


class InferKnowledgeResponse(BaseModel):
    offer_id: uuid.UUID
    offer_name: str
    suggestions: dict[str, list[InferredKnowledgeItem]]


class ExistingKnowledgeItem(BaseModel):
    knowledge_type: str
    title: str
    content_raw: str = ""


class InferOfferKnowledgeRequest(BaseModel):
    """Infer knowledge from offer info. Works for both creation and update."""
    name: str
    offer_type: str = "product"
    description: str = ""
    language: str = "zh-CN"
    existing_knowledge: list[ExistingKnowledgeItem] | None = None


class InferOfferKnowledgeResponse(BaseModel):
    offer_name: str
    description: str | None = None
    suggestions: dict[str, list[InferredKnowledgeItem]]


class ExtractTextResponse(BaseModel):
    text: str
    source: str  # "file" | "url"
    filename: str | None = None
