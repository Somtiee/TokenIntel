"""Swarms multi-agent workflow for TokenIntel research."""

from __future__ import annotations

import json
import time
from typing import Any

from loguru import logger
from swarms import Agent, SequentialWorkflow

from config import Settings, get_settings
from models import ResearchRequest, WorkflowResult
from tools import (
    all_agent_tools,
    birdeye_token_overview,
    fetch_token_news_free,
    helius_token_metadata,
    is_valid_mint_address,
    lookup_token_identity,
    token_social_sentiment,
)
from utils import enrich_report_from_tools, workflow_result_to_report


def _agent_common_kwargs(settings: Settings) -> dict[str, Any]:
    return {
        "model_name": settings.llm_model,
        "max_loops": 2,
        "verbose": False,
        "autosave": False,
    }


def build_data_collector(settings: Settings | None = None) -> Agent:
    """Agent 1: gather on-chain and market data via tools."""
    settings = settings or get_settings()
    tools = [
        birdeye_token_overview,
        helius_token_metadata,
    ]
    return Agent(
        agent_name="TokenIntel-DataCollector",
        system_prompt=(
            "You are a Web3 data collector for TokenIntel ($TINTEL). "
            "Use tools to fetch token overview and on-chain metadata. "
            "Return concise JSON-friendly bullet facts: price, liquidity, market cap, "
            "mint/freeze authority status, and notable flags. Never invent numbers."
        ),
        tools=tools,
        **_agent_common_kwargs(settings),
    )


def build_onchain_analyst(settings: Settings | None = None) -> Agent:
    """Agent 2: interpret on-chain risk signals."""
    settings = settings or get_settings()
    return Agent(
        agent_name="TokenIntel-OnChainAnalyst",
        system_prompt=(
            "You are an on-chain security analyst. Given prior research context, "
            "assess holder concentration, authority risks, deployer behavior, and liquidity traps. "
            "Output markdown with ## On-Chain Analysis and bullet risks."
        ),
        **_agent_common_kwargs(settings),
    )


def build_sentiment_analyst(settings: Settings | None = None) -> Agent:
    """Agent 3: social sentiment via X."""
    settings = settings or get_settings()
    return Agent(
        agent_name="TokenIntel-SentimentAnalyst",
        system_prompt=(
            "You are a crypto social sentiment analyst. Always call token_social_sentiment first "
            "(X when X_BEARER_TOKEN is set, plus Reddit). Summarize narrative, volume, and sentiment skew. "
            "Output markdown with ## Social Sentiment and a score from -1 (bearish) to +1 (bullish)."
        ),
        tools=[token_social_sentiment],
        **_agent_common_kwargs(settings),
    )


def build_report_writer(settings: Settings | None = None) -> Agent:
    """Agent 4: synthesize institutional-grade report."""
    settings = settings or get_settings()
    return Agent(
        agent_name="TokenIntel-ReportWriter",
        system_prompt=(
            "You are the lead author for TokenIntel ($TINTEL) research reports. "
            "Synthesize all prior agent outputs into a single markdown report with sections:\n"
            "## Executive Summary\n"
            "## Market Snapshot\n"
            "## On-Chain Analysis\n"
            "## Social Sentiment\n"
            "## Bullish Thesis\n"
            "## Bearish Thesis\n"
            "## Risk Factors\n"
            "## Verdict\n"
            "State risk level explicitly (low/medium/high/critical). Be factual; cite uncertainty."
        ),
        **_agent_common_kwargs(settings),
    )


def build_workflow(settings: Settings | None = None) -> SequentialWorkflow:
    """Sequential pipeline: collect → on-chain → sentiment → write."""
    settings = settings or get_settings()
    agents = [
        build_data_collector(settings),
        build_onchain_analyst(settings),
        build_sentiment_analyst(settings),
        build_report_writer(settings),
    ]
    return SequentialWorkflow(
        name="TokenIntel-Research-Pipeline",
        description="Multi-agent Web3 research report generation for $TINTEL",
        agents=agents,
        max_loops=1,
        verbose=False,
        autosave=False,
    )


def _task_prompt(request: ResearchRequest) -> str:
    social = "enabled" if request.include_social else "disabled"
    onchain = "enabled" if request.include_onchain else "disabled"
    return (
        f"Produce a TokenIntel research run for token address {request.token_address} "
        f"on chain {request.chain}. Depth: {request.depth.value}. "
        f"On-chain analysis: {onchain}. Social sentiment: {social}. "
        f"Symbol hint: {request.symbol_hint or 'unknown'}."
    )


def run_research(request: ResearchRequest, settings: Settings | None = None) -> WorkflowResult:
    """
    Execute the full Swarms sequential workflow and return a structured result.

    Raises:
        ValueError: If no LLM provider is configured.
    """
    settings = settings or get_settings()
    settings.require_llm_provider()

    if not is_valid_mint_address(request.token_address):
        return WorkflowResult(
            success=False,
            error_message=(
                f"Invalid token address '{request.token_address}'. "
                "Resolve a Birdeye-verified symbol or paste the full mint address."
            ),
            elapsed_seconds=0.0,
        )

    start = time.perf_counter()
    workflow = build_workflow(settings)
    task = _task_prompt(request)

    logger.info("Starting TokenIntel workflow for {}", request.token_address)
    try:
        raw = workflow.run(task)
        raw_text = raw if isinstance(raw, str) else json.dumps(raw, default=str)
    except Exception as exc:
        logger.exception("Workflow failed")
        return WorkflowResult(
            success=False,
            error_message=str(exc),
            elapsed_seconds=time.perf_counter() - start,
        )

    report = workflow_result_to_report(
        token_address=request.token_address,
        chain=request.chain,
        raw_output=raw_text,
        symbol_hint=request.symbol_hint,
    )

    identity = lookup_token_identity(request.token_address, request.chain)
    symbol = (request.symbol_hint or identity.get("symbol") or "").upper()
    token_name = identity.get("name") or ""
    if symbol and not report.symbol:
        report.symbol = symbol
    if token_name and not report.name:
        report.name = token_name

    overview_json: str | None = None
    social_json: str | None = None
    news_json: str | None = None
    try:
        overview_json = birdeye_token_overview(request.token_address, request.chain)
        if request.include_social and symbol:
            social_json = token_social_sentiment(f"${symbol}", symbol=symbol)
        if symbol:
            news_json = fetch_token_news_free(symbol)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Post-run tool enrichment skipped: {}", exc)

    report = enrich_report_from_tools(report, overview_json, social_json, news_json)

    elapsed = time.perf_counter() - start
    logger.info("Workflow completed in {:.2f}s", elapsed)
    return WorkflowResult(
        success=True,
        report=report,
        raw_agent_output=raw_text,
        elapsed_seconds=elapsed,
    )


def list_registered_tools() -> list[str]:
    """Return tool function names for UI display."""
    return [fn.__name__ for fn in all_agent_tools()]
