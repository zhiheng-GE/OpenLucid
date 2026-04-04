import uuid
from pydantic import BaseModel, Field

from app.schemas.knowledge import KnowledgeItemResponse
from app.schemas.asset import AssetResponse


class StrategyUnitCoverageReview(BaseModel):
    unit_id: uuid.UUID
    offer_id: uuid.UUID
    total_offer_knowledge: int
    linked_knowledge: int
    knowledge_coverage: float
    total_offer_assets: int
    linked_assets: int
    asset_coverage: float
    topic_count: int
    next_action: str
    next_action_label: str
    is_ready_to_generate: bool


class RecommendedKnowledgeResponse(BaseModel):
    unit_id: uuid.UUID
    offer_id: uuid.UUID
    items: list[KnowledgeItemResponse]
    total: int


class RecommendedAssetsResponse(BaseModel):
    unit_id: uuid.UUID
    offer_id: uuid.UUID
    items: list[AssetResponse]
    total: int


class StrategyUnitGenerateTopicsRequest(BaseModel):
    channel: str | None = None
    language: str = "zh-CN"
    count: int = Field(5, ge=1, le=20)


class OfferCompletenessScore(BaseModel):
    """Per-offer score (max 85): profile(20) + knowledge(35) + strategy(15) + assets(15)."""
    total: int = 0
    profile: int = 0
    knowledge: int = 0
    strategy: int = 0
    assets: int = 0
    next_action: str = "add_description"


class MerchantCompletenessResponse(BaseModel):
    """Company-level score (max 100): avg offer scores (0-85) + company brandkit (0-15)."""
    company_total: int = 0
    brandkit: int = 0
    offer_avg: int = 0
    next_action: str = "add_description"
    offers: dict[str, OfferCompletenessScore] = {}


class OfferCoverageReview(BaseModel):
    offer_id: uuid.UUID
    knowledge_count: int
    knowledge_by_type: dict[str, int]
    asset_count: int
    asset_by_type: dict[str, int]
    strategy_unit_count: int
    topic_count: int
    missing: list[str]
    readiness_score: float
    next_action: str
    next_action_label: str
