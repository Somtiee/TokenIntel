"""Pydantic v2 domain models for TokenIntel research reports."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class ResearchDepth(str, Enum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class ResearchRequest(BaseModel):
    """User input for a research run."""

    token_address: str = Field(..., min_length=32, max_length=64, description="Token mint / contract address.")
    chain: str = Field(default="solana", description="Blockchain network identifier.")
    symbol_hint: str | None = Field(default=None, max_length=16)
    include_social: bool = Field(default=True)
    include_onchain: bool = Field(default=True)
    depth: ResearchDepth = Field(default=ResearchDepth.STANDARD)

    @field_validator("token_address")
    @classmethod
    def strip_address(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("token_address cannot be empty")
        return cleaned


class PriceSnapshot(BaseModel):
    price_usd: float | None = None
    change_24h_pct: float | None = None
    volume_24h_usd: float | None = None
    market_cap_usd: float | None = None
    liquidity_usd: float | None = None
    source: str = "birdeye"


class OnChainMetrics(BaseModel):
    holder_count: int | None = None
    top_holder_concentration_pct: float | None = None
    mint_authority_revoked: bool | None = None
    freeze_authority_revoked: bool | None = None
    deployer: str | None = None
    notes: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class SocialSentiment(BaseModel):
    mention_count: int = 0
    sentiment_score: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        description="Normalized sentiment from -1 (bearish) to +1 (bullish).",
    )
    sample_tweets: list[str] = Field(default_factory=list)
    influencers: list[str] = Field(default_factory=list)
    source: str = "x"


class AgentSection(BaseModel):
    """Output fragment from a single swarm agent."""

    agent_name: str
    title: str
    content_markdown: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    data_sources: list[str] = Field(default_factory=list)


class ResearchReport(BaseModel):
    """Aggregated multi-agent research deliverable."""

    token_address: str
    chain: str
    symbol: str | None = None
    name: str | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    executive_summary: str
    risk_level: RiskLevel = RiskLevel.UNKNOWN
    risk_factors: list[str] = Field(default_factory=list)
    bullish_thesis: list[str] = Field(default_factory=list)
    bearish_thesis: list[str] = Field(default_factory=list)
    price: PriceSnapshot | None = None
    onchain: OnChainMetrics | None = None
    social: SocialSentiment | None = None
    sections: list[AgentSection] = Field(default_factory=list)
    full_report_markdown: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_display_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class WorkflowResult(BaseModel):
    """Raw + structured output from the Swarms pipeline."""

    success: bool
    report: ResearchReport | None = None
    raw_agent_output: str | None = None
    error_message: str | None = None
    elapsed_seconds: float | None = None


# ---------------------------------------------------------------------------
# FullResearchReport pipeline models (agents/utils)
# ---------------------------------------------------------------------------


class OnChainData(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    price: float | None = None
    mc: float | None = None
    liquidity: float | None = None
    holders: int | None = None
    volume_24h: float | None = None
    change_24h: float | None = None
    security_score: float | None = None
    token_age_days: float | None = None
    top_holders_summary: list[dict[str, Any]] = Field(default_factory=list)
    ohlcv_last_1h: list[dict[str, Any]] = Field(default_factory=list)


class TweetData(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str
    created_at: str
    likes: int = Field(ge=0)
    retweets: int = Field(ge=0)
    author: dict[str, Any]
    url: str


class NewsItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str
    url: str | None = None
    source: str
    timestamp: str | None = None


class SentimentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    overall: str
    score: float = Field(ge=-1.0, le=1.0)
    key_bullish: list[str] = Field(default_factory=list)
    key_bearish: list[str] = Field(default_factory=list)


class ReportSection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str
    content: str
    data: dict[str, Any] | None = None


class FullResearchReport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    token_symbol: str
    token_name: str = ""
    token_mint: str
    timestamp: str
    onchain: OnChainData
    tweets: list[TweetData] = Field(default_factory=list)
    news: list[NewsItem] = Field(default_factory=list)
    sentiment: SentimentSummary
    sections: list[ReportSection] = Field(default_factory=list)
    recommendation: str
    confidence: float = Field(ge=0.0, le=1.0)
    sources: list[str] = Field(default_factory=list)
