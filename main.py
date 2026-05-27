"""
TokenIntel ($TINTEL) — Streamlit UI entrypoint.

Run: streamlit run main.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import urllib.parse
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import streamlit as st
from loguru import logger

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_ASSETS_DIR = _ROOT / "assets"
_LOGO_PATH = _ASSETS_DIR / "logo.png"
_FAVICON_PATH = _ASSETS_DIR / "favicon.png"

from agents import build_workflow, list_registered_tools, run_research
from config import get_settings, reload_settings
from models import (
    FullResearchReport,
    NewsItem,
    OnChainData,
    ResearchDepth,
    ResearchReport,
    ResearchRequest,
    ReportSection,
    SentimentSummary,
    TweetData,
    WorkflowResult,
)
from tools import is_valid_mint_address, lookup_token_identity, resolve_token_mint
from utils import (
    _build_plotly_figure,
    build_executive_recommendation,
    build_price_chart,
    build_sentiment_gauge,
    build_sentiment_summary_text,
    clean_prose_for_display,
    compute_sentiment_score,
    configure_logging,
    display_recommendation,
    extract_workflow_markdown,
    generate_price_chart,
    parse_price_from_tool_json,
    report_to_markdown,
    report_to_share_card,
    share_snippet_for_x,
)

# ---------------------------------------------------------------------------
# Page config (must be first Streamlit call)
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="TokenIntel $TINTEL",
    page_icon=str(_FAVICON_PATH) if _FAVICON_PATH.is_file() else "🔍",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Theme (dark only)
# ---------------------------------------------------------------------------

_THEME = {
    "bg": "#0B1020",
    "panel": "#121A2B",
    "text": "#E6EDF7",
    "muted": "#94A3B8",
    "accent": "#9945FF",
    "accent2": "#14F195",
    "border": "rgba(148,163,184,0.2)",
}


def _logo_data_uri() -> str | None:
    if not _LOGO_PATH.is_file():
        return None
    encoded = base64.b64encode(_LOGO_PATH.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _inject_theme_css() -> None:
    t = _THEME
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        html, body, [class*="css"] {{ font-family: 'Inter', sans-serif; }}
        .stApp {{ background: {t["bg"]}; color: {t["text"]}; }}
        .main .block-container {{
            padding-top: 1rem;
            padding-left: max(1rem, env(safe-area-inset-left));
            padding-right: max(1rem, env(safe-area-inset-right));
            max-width: 100%;
        }}
        [data-testid="stSidebar"] {{
            background: linear-gradient(180deg, {t["panel"]} 0%, {t["bg"]} 100%);
            border-right: 1px solid {t["border"]};
        }}
        .tintel-sidebar-brand {{
            text-align: center;
            margin-bottom: 0.35rem;
        }}
        .tintel-sidebar-brand img {{
            max-width: 72px;
            width: 100%;
            height: auto;
            display: block;
            margin: 0 auto;
        }}
        .tintel-sidebar-title {{
            text-align: center;
            margin: 0.25rem 0 0.75rem 0;
            font-size: 1.05rem;
            font-weight: 700;
            color: {t["text"]};
        }}
        .tintel-sidebar-tag {{
            text-align: center;
            color: {t["muted"]};
            font-size: 0.78rem;
            margin: -0.35rem 0 0.85rem 0;
        }}
        .tintel-sidebar-about {{
            background: rgba(153,69,255,0.12);
            border: 1px solid {t["border"]};
            border-radius: 12px;
            padding: 0.85rem 0.9rem;
            margin-bottom: 0.75rem;
            font-size: 0.84rem;
            line-height: 1.45;
            color: #c7d2fe;
        }}
        .tintel-sidebar-about strong {{
            color: {t["accent2"]};
            display: block;
            margin-bottom: 0.35rem;
            font-size: 0.88rem;
        }}
        .tintel-sidebar-about ul {{
            margin: 0.35rem 0 0 1rem;
            padding: 0;
        }}
        .tintel-sidebar-about li {{
            margin-bottom: 0.3rem;
        }}
        .tintel-hero {{
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 1.1rem;
            background: linear-gradient(135deg, {t["bg"]} 0%, #312e81 45%, {t["accent"]} 130%);
            padding: 2rem 2.25rem;
            border-radius: 18px;
            margin-bottom: 1.25rem;
            border: 1px solid {t["border"]};
            box-shadow: 0 20px 50px rgba(0,0,0,0.25);
        }}
        .tintel-hero-logo {{
            width: 80px;
            height: 80px;
            object-fit: contain;
            flex-shrink: 0;
        }}
        .tintel-hero-body {{ flex: 1; min-width: min(100%, 220px); }}
        .tintel-hero h1 {{ color: #f8fafc; margin: 0; font-size: clamp(1.2rem, 4vw, 2.1rem); letter-spacing: -0.02em; }}
        .tintel-hero p {{ color: #c7d2fe; margin: 0.45rem 0 0 0; font-size: clamp(0.88rem, 2.5vw, 1rem); }}
        .tintel-pill {{
            display: inline-block;
            padding: 0.25rem 0.65rem;
            border-radius: 999px;
            background: rgba(20,241,149,0.15);
            color: {t["accent2"]};
            font-size: 0.75rem;
            font-weight: 600;
            margin-right: 0.35rem;
        }}
        div[data-testid="stMetric"] {{
            background: {t["panel"]};
            border: 1px solid {t["border"]};
            border-radius: 12px;
            padding: 0.75rem 1rem;
        }}
        [data-testid="stImage"] img {{
            max-width: 100%;
            height: auto;
        }}
        .stButton > button, button[kind="primary"] {{
            min-height: 44px;
        }}
        .stTabs [data-baseweb="tab-list"] {{
            gap: 8px;
            flex-wrap: wrap;
        }}
        .stTabs [data-baseweb="tab"] {{
            border-radius: 10px 10px 0 0;
            padding: 10px 18px;
            font-weight: 600;
        }}
        footer.tintel-footer {{
            text-align: center;
            color: {t["muted"]};
            font-size: 0.85rem;
            margin-top: 2rem;
            padding: 1rem 0 2rem 0;
        }}
        @media (max-width: 768px) {{
            [data-testid="stSidebar"] > div {{
                padding-top: 0.5rem;
                padding-left: 0.65rem;
                padding-right: 0.65rem;
            }}
            .tintel-sidebar-brand img {{ max-width: 56px; }}
            .tintel-sidebar-about {{ font-size: 0.8rem; padding: 0.7rem; }}
            .tintel-hero {{
                flex-direction: column;
                text-align: center;
                padding: 1.25rem 1rem;
            }}
            .tintel-hero-logo {{ width: 68px; height: 68px; }}
            .tintel-pill {{ display: none; }}
            .stTabs [data-baseweb="tab"] {{
                padding: 8px 12px;
                font-size: 0.85rem;
            }}
            div[data-testid="stMetric"] {{ padding: 0.55rem 0.7rem; }}
        }}
        @media (max-width: 480px) {{
            .main .block-container {{ padding-left: 0.75rem; padding-right: 0.75rem; }}
            .tintel-hero-logo {{ width: 56px; height: 56px; }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Session bootstrap
# ---------------------------------------------------------------------------


def _init_session() -> None:
    defaults: dict[str, Any] = {
        "llm_model": "gpt-4o",
        "chain": "solana",
        "depth": "standard",
        "include_onchain": True,
        "include_social": True,
        "last_report": None,
        "last_full_report": None,
        "last_workflow": None,
        "last_elapsed": None,
        "history": [],
        "query_input": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    if not st.session_state.get("_env_bootstrapped"):
        reload_settings()
        settings = get_settings()
        st.session_state.llm_model = settings.llm_model
        st.session_state._env_bootstrapped = True


@st.cache_resource(show_spinner=False)
def _cached_workflow(llm_model: str) -> Any:
    """Cache Swarms SequentialWorkflow by model name."""
    os.environ["TINTEL_LLM_MODEL"] = llm_model
    reload_settings()
    return build_workflow()


@st.cache_resource(show_spinner=False)
def _cached_logging(level: str) -> None:
    configure_logging(level)
    return True


def _apply_session_api_keys() -> None:
    """Apply optional sidebar overrides only (never reads secrets into UI state)."""
    mapping = {
        "OPENAI_API_KEY": "ov_openai",
        "GROQ_API_KEY": "ov_groq",
        "ANTHROPIC_API_KEY": "ov_anthropic",
        "BIRDEYE_API_KEY": "ov_birdeye",
        "HELIUS_API_KEY": "ov_helius",
        "X_BEARER_TOKEN": "ov_x",
    }
    for env_key, state_key in mapping.items():
        value = (st.session_state.get(state_key) or "").strip()
        if value:
            os.environ[env_key] = value
    model = (st.session_state.get("llm_model") or "").strip()
    if model:
        os.environ["TINTEL_LLM_MODEL"] = model
    reload_settings()


def _has_llm_from_session() -> bool:
    _apply_session_api_keys()
    return get_settings().has_llm_provider()


def _credential_status() -> dict[str, bool]:
    _apply_session_api_keys()
    s = get_settings()
    return {
        "OpenAI": bool(s.secret_or_none(s.openai_api_key)),
        "Groq": bool(s.secret_or_none(s.groq_api_key)),
        "Anthropic": bool(s.secret_or_none(s.anthropic_api_key)),
        "Birdeye": bool(s.secret_or_none(s.birdeye_api_key)),
        "Helius": bool(s.secret_or_none(s.helius_api_key)),
        "X (Bearer)": s.has_x_api(),
    }


@st.cache_data(ttl=300, show_spinner=False)
def _x_connection_status() -> dict[str, Any]:
    from tools import verify_x_connection

    reload_settings()
    return verify_x_connection()


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_resolve(query: str, chain: str) -> dict[str, str]:
    return resolve_token_mint(query, chain=chain)


def _parse_user_query(raw: str, chain: str = "solana") -> tuple[str, str, str | None, str]:
    """
    Returns (kind, mint, symbol, name).
    """
    resolved = _cached_resolve(raw.strip(), chain)
    mint = resolved["mint"]
    symbol = (resolved.get("symbol") or "").strip().upper() or None
    name = (resolved.get("name") or "").strip()
    if not symbol and is_valid_mint_address(mint):
        meta = lookup_token_identity(mint, chain)
        symbol = (meta.get("symbol") or "").upper() or None
        name = name or (meta.get("name") or "").strip()
    kind = "mint" if resolved.get("source") == "mint" else "symbol"
    return kind, mint, symbol, name


def _token_display_label(symbol: str | None, name: str, mint: str) -> str:
    sym = (symbol or "").upper().replace("$", "")
    if sym and name:
        return f"${sym} · {name}"
    if sym:
        return f"${sym}"
    if name:
        return name
    return f"{mint[:6]}…{mint[-4:]}"


def _research_to_full_report(rr: ResearchReport, raw_output: str | None = None) -> FullResearchReport:
    """Bridge legacy ResearchReport → FullResearchReport for premium UI tabs."""
    from tools import birdeye_token_overview

    ts = rr.generated_at.astimezone(timezone.utc).isoformat()
    price = rr.price
    if price is None and is_valid_mint_address(rr.token_address):
        try:
            overview = birdeye_token_overview(rr.token_address, rr.chain)
            price = parse_price_from_tool_json(overview)
        except Exception:
            pass

    md_body = rr.full_report_markdown or ""
    if raw_output and _looks_like_dump(raw_output):
        md_body = extract_workflow_markdown(raw_output)
    elif _looks_like_dump(md_body):
        md_body = extract_workflow_markdown(md_body)

    confidence = 0.65
    if rr.risk_level.value in ("low",):
        confidence = 0.78
    elif rr.risk_level.value in ("high", "critical"):
        confidence = 0.55

    sym = (rr.symbol or "").upper().replace("$", "") or rr.token_address[:6]
    token_name = (rr.name or "").strip()
    if not token_name and is_valid_mint_address(rr.token_address):
        token_name = lookup_token_identity(rr.token_address, rr.chain).get("name", "")

    change_24h = price.change_24h_pct if price else None
    score = compute_sentiment_score(
        social=rr.social,
        social_json=None,
        md_body=md_body,
        price_change_24h=change_24h,
    )

    executive = build_executive_recommendation(
        symbol=sym,
        mint=rr.token_address,
        risk_level=rr.risk_level,
        price=price,
        bullish=rr.bullish_thesis,
        bearish=rr.bearish_thesis,
        confidence=confidence,
        md_body=md_body,
        legacy_executive=rr.executive_summary,
    )

    onchain = OnChainData(
        price=price.price_usd if price else None,
        mc=price.market_cap_usd if price else None,
        liquidity=price.liquidity_usd if price else None,
        volume_24h=price.volume_24h_usd if price else None,
        change_24h=price.change_24h_pct if price else None,
        holders=rr.onchain.holder_count if rr.onchain else None,
        security_score=None,
        token_age_days=None,
        top_holders_summary=[],
        ohlcv_last_1h=[],
    )

    sentiment_text = build_sentiment_summary_text(
        md_body=md_body,
        score=score,
        bullish=rr.bullish_thesis,
        bearish=rr.bearish_thesis,
    )
    sentiment_overall = clean_prose_for_display(sentiment_text, max_chars=320, mint=rr.token_address)
    if not sentiment_overall:
        sentiment_overall = build_sentiment_summary_text(
            md_body=md_body,
            score=score,
            bullish=rr.bullish_thesis,
            bearish=rr.bearish_thesis,
        )

    sentiment = SentimentSummary(
        overall=sentiment_overall,
        score=score,
        key_bullish=rr.bullish_thesis[:8],
        key_bearish=rr.bearish_thesis[:8],
    )

    news_items: list[NewsItem] = []
    for item in rr.metadata.get("news", [])[:12]:
        if isinstance(item, dict) and item.get("title"):
            news_items.append(
                NewsItem(
                    title=str(item["title"]),
                    url=item.get("url"),
                    source=str(item.get("source", "rss")),
                    timestamp=item.get("timestamp"),
                )
            )

    tweets: list[TweetData] = []
    if rr.social and rr.social.sample_tweets:
        for text in rr.social.sample_tweets[:10]:
            tweets.append(
                TweetData(
                    text=text,
                    created_at=ts,
                    likes=0,
                    retweets=0,
                    author={"username": "social", "name": rr.social.source or "reddit"},
                    url=f"https://www.reddit.com/search/?q={urllib.parse.quote(sym)}",
                )
            )

    sections = [
        ReportSection(
            title=s.title,
            content=s.content_markdown,
            data={"agent": s.agent_name, "confidence": s.confidence},
        )
        for s in rr.sections
    ]

    sources = list_registered_tools()
    if price:
        sources.append(f"market:{price.source}")

    full = FullResearchReport(
        token_symbol=sym,
        token_name=token_name,
        token_mint=rr.token_address,
        timestamp=ts,
        onchain=onchain,
        tweets=tweets,
        news=news_items,
        sentiment=sentiment,
        sections=sections,
        recommendation=executive,
        confidence=confidence,
        sources=sources,
    )
    return full.model_copy(update={"recommendation": display_recommendation(full)})


def _looks_like_dump(text: str) -> bool:
    s = (text or "").strip()
    return s.startswith("[") and '"role"' in s[:400]


def _append_history(label: str, full: FullResearchReport, workflow: WorkflowResult | None) -> None:
    entry = {
        "label": label,
        "timestamp": full.timestamp,
        "mint": full.token_mint,
        "symbol": full.token_symbol,
        "report": full.model_dump(mode="json"),
        "elapsed": workflow.elapsed_seconds if workflow else None,
    }
    history: list[dict[str, Any]] = st.session_state.get("history", [])
    history = [h for h in history if h.get("mint") != full.token_mint]
    history.insert(0, entry)
    st.session_state.history = history[:12]


def _fmt_history_time(ts: str | None) -> str:
    if not ts:
        return "Unknown time"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts


def _render_recent_searches_panel() -> None:
    history: list[dict[str, Any]] = st.session_state.get("history", [])
    if not history:
        return

    st.markdown("### Recent Searches")
    page_size = 5
    total = len(history)
    pages = max(1, (total + page_size - 1) // page_size)
    page = int(st.session_state.get("history_page", 0))
    page = max(0, min(page, pages - 1))
    st.session_state.history_page = page

    start = page * page_size
    end = min(total, start + page_size)
    for i in range(start, end):
        item = history[i]
        label = item.get("label", item.get("symbol", "Report"))
        when = _fmt_history_time(item.get("timestamp"))
        btn_label = f"{when} · {label}"
        if st.button(btn_label, key=f"recent_main_{i}", use_container_width=True):
            st.session_state.last_full_report = FullResearchReport.model_validate(item["report"])
            st.session_state.query_input = f"${item.get('symbol')}" if item.get("symbol") else item.get("mint", "")

    c_prev, c_info, c_next = st.columns([1, 2, 1])
    with c_prev:
        if st.button("Prev", key="history_prev", use_container_width=True, disabled=page <= 0):
            st.session_state.history_page = max(0, page - 1)
            st.rerun()
    with c_info:
        st.caption(f"Page {page + 1} / {pages}")
    with c_next:
        if st.button("Next", key="history_next", use_container_width=True, disabled=page >= pages - 1):
            st.session_state.history_page = min(pages - 1, page + 1)
            st.rerun()


def _clipboard_button(text: str, label: str = "Copy to Clipboard") -> None:
    escaped = json.dumps(text)
    st.components.v1.html(
        f"""
        <button id="copy-btn" style="
            width:100%;
            padding:0.65rem 1rem;
            border-radius:10px;
            border:none;
            background:linear-gradient(135deg,#6366f1,#9945FF);
            color:white;
            font-weight:600;
            cursor:pointer;
        ">{label}</button>
        <script>
        const btn = document.getElementById('copy-btn');
        btn.onclick = () => {{
            navigator.clipboard.writeText({escaped});
            btn.innerText = 'Copied!';
            setTimeout(() => btn.innerText = '{label}', 2000);
        }};
        </script>
        """,
        height=70,
    )


def _share_on_x(full: FullResearchReport) -> None:
    snippet = share_snippet_for_x(full)
    url = "https://twitter.com/intent/tweet?" + urllib.parse.urlencode({"text": snippet})
    st.link_button("Post text on X (attach JPEG below)", url, use_container_width=True)


def _fmt_share_price(full: FullResearchReport) -> str:
    p = full.onchain.price
    if p is None:
        return "N/A"
    if p >= 1:
        return f"${p:,.4f}"
    return f"${p:.6f}"


def _render_sidebar() -> None:
    with st.sidebar:
        logo_uri = _logo_data_uri()
        if logo_uri:
            st.markdown(
                f'<div class="tintel-sidebar-brand"><img src="{logo_uri}" alt="TokenIntel logo" /></div>',
                unsafe_allow_html=True,
            )
        st.markdown('<p class="tintel-sidebar-title">TokenIntel</p>', unsafe_allow_html=True)
        st.markdown('<p class="tintel-sidebar-tag">$TINTEL · AI token research</p>', unsafe_allow_html=True)

        st.markdown(
            """
            <div class="tintel-sidebar-about">
              <strong>What this app does</strong>
              <ul>
                <li>Research any token by <b>symbol</b> or <b>mint address</b></li>
                <li>Four AI agents combine market, on-chain, social &amp; news signals</li>
                <li>Get an executive summary, charts &amp; a shareable report card</li>
                <li>Built for traders &amp; builders — not financial advice</li>
              </ul>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.expander("Research options", expanded=False):
            st.session_state.chain = st.selectbox(
                "Chain",
                ["solana", "ethereum", "bsc", "base"],
                index=["solana", "ethereum", "bsc", "base"].index(st.session_state.chain),
            )
            st.session_state.depth = st.selectbox(
                "Depth",
                [d.value for d in ResearchDepth],
                index=[d.value for d in ResearchDepth].index(st.session_state.depth),
            )
            st.session_state.llm_model = st.text_input(
                "LLM model",
                value=st.session_state.llm_model,
                help="Use a model your API key can access (e.g. gpt-4o-mini).",
            )
            st.session_state.include_onchain = st.checkbox(
                "On-chain data", value=st.session_state.include_onchain
            )
            st.session_state.include_social = st.checkbox(
                "Social & news", value=st.session_state.include_social
            )

        history: list[dict[str, Any]] = st.session_state.get("history", [])
        if history:
            with st.expander("Recent reports", expanded=False):
                for i, item in enumerate(history):
                    label = item.get("label", item.get("symbol", "Report"))
                    if st.button(label, key=f"hist_{i}", use_container_width=True):
                        st.session_state.last_full_report = FullResearchReport.model_validate(
                            item["report"]
                        )
                        st.session_state.query_input = (
                            f"${item.get('symbol')}" if item.get("symbol") else item.get("mint", "")
                        )


def _render_hero() -> None:
    logo_uri = _logo_data_uri()
    logo_html = (
        f'<img class="tintel-hero-logo" src="{logo_uri}" alt="TokenIntel logo" />'
        if logo_uri
        else ""
    )
    st.markdown(
        f"""
        <div class="tintel-hero">
            {logo_html}
            <div class="tintel-hero-body">
                <h1>TokenIntel Research Studio</h1>
                <p>Enter a token, run multi-agent research, and get a clear report in minutes.</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _run_pipeline(query: str) -> None:
    _apply_session_api_keys()
    if not _has_llm_from_session():
        st.error("Add at least one LLM API key to `.env` (OPENAI_API_KEY, GROQ_API_KEY, or ANTHROPIC_API_KEY).")
        return

    try:
        _kind, mint, symbol, token_name = _parse_user_query(query, chain=st.session_state.chain)
    except ValueError as exc:
        st.warning(str(exc))
        return

    st.session_state.resolved_token_label = _token_display_label(symbol, token_name, mint)
    st.success(f"Resolved: **{st.session_state.resolved_token_label}**")

    if not is_valid_mint_address(mint):
        st.error(
            "Could not resolve a valid Solana mint. Use a Birdeye-verified symbol (e.g. JUP) "
            "with BIRDEYE_API_KEY in `.env`, or paste the full contract address."
        )
        return

    request = ResearchRequest(
        token_address=mint,
        chain=st.session_state.chain,
        symbol_hint=symbol,
        include_onchain=st.session_state.include_onchain,
        include_social=st.session_state.include_social,
        depth=ResearchDepth(st.session_state.depth),
    )

    _cached_workflow(st.session_state.llm_model)
    _cached_logging(get_settings().tintel_log_level)

    progress = st.progress(0, text="Initializing TokenIntel pipeline…")
    status = st.empty()

    try:
        status.info("Running multi-agent research — typically **2–5 minutes**. Please wait.")
        progress.progress(15, text="Collecting on-chain & market data…")

        with st.spinner("TokenIntel agents at work…"):
            result: WorkflowResult = run_research(request)

        progress.progress(85, text="Formatting report…")

        if not result.success or result.report is None:
            err_msg = result.error_message or "Research workflow failed. Check logs and API keys."
            lower = err_msg.lower()
            if any(
                k in lower
                for k in ["model", "not found", "invalid_request_error", "insufficient_quota", "permission"]
            ):
                err_msg += (
                    "\n\nTip: your key may not have access to the selected model. "
                    "Open sidebar -> Research options -> set LLM model to `gpt-4o-mini`, then retry."
                )
            st.error(err_msg)
            progress.empty()
            status.empty()
            return

        full = _research_to_full_report(result.report, result.raw_agent_output)
        md = report_to_markdown(full)

        try:
            st.session_state.last_share_card = report_to_share_card(full)
        except Exception as exc:
            logger.warning("Share card generation failed: {}", exc)
            st.session_state.last_share_card = None

        st.session_state.last_report = result.report
        st.session_state.last_full_report = full
        st.session_state.last_workflow = result
        st.session_state.last_elapsed = result.elapsed_seconds
        st.session_state.last_markdown = md

        label = _token_display_label(symbol or full.token_symbol, token_name or full.token_name, mint)
        _append_history(label, full, result)

        progress.progress(100, text="Done")
        status.success(f"Report generated in **{result.elapsed_seconds:.1f}s** for {label}.")
        progress.empty()

    except Exception as exc:
        logger.exception("UI pipeline failed")
        st.error(f"Something went wrong: {exc}")
        progress.empty()
        status.empty()


def _render_results() -> None:
    full: FullResearchReport | None = st.session_state.get("last_full_report")
    if full is None:
        st.info("Enter a token symbol or mint and click **Generate Research Report** to begin.")
        return

    md = st.session_state.get("last_markdown") or report_to_markdown(full)
    elapsed = st.session_state.get("last_elapsed")

    c1, c2, c3, c4 = st.columns(4)
    token_label = _token_display_label(full.token_symbol, full.token_name, full.token_mint)
    c1.metric("Token", token_label)
    c2.metric("Confidence", f"{full.confidence:.0%}")
    c3.metric("Sentiment", f"{full.sentiment.score:+.2f}")
    c4.metric("Runtime", f"{elapsed:.1f}s" if elapsed else "—")

    tab_md, tab_charts, tab_raw, tab_sources = st.tabs(
        ["📄 Report Markdown", "📈 Interactive Charts", "🧬 Raw Data", "🔗 Sources"]
    )

    with tab_md:
        st.markdown(md)
        card_bytes: bytes | None = st.session_state.get("last_share_card")
        if card_bytes:
            try:
                st.image(BytesIO(card_bytes), caption="Share card preview", use_container_width=True)
            except Exception as exc:
                logger.warning("Share card preview render failed: {}", exc)
                st.caption("Share card preview unavailable in this environment.")
        st.divider()
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            if card_bytes:
                st.download_button(
                    "Download share card (JPEG)",
                    data=card_bytes,
                    file_name=f"tokenintel_{full.token_symbol}_{full.token_mint[:8]}.jpg",
                    mime="image/jpeg",
                    use_container_width=True,
                )
            else:
                st.caption("Share card unavailable for this run.")
        with col_b:
            _share_on_x(full)
        with col_c:
            _clipboard_button(md, label="Copy report text")
        with st.expander("Download Markdown (optional)"):
            st.download_button(
                "Download .md",
                data=md,
                file_name=f"tokenintel_{full.token_mint[:8]}.md",
                mime="text/markdown",
                use_container_width=True,
            )
        st.caption(
            "PDF export is disabled on Windows (WeasyPrint needs Linux GTK libraries). "
            "Use the JPEG share card for X and social posts."
        )

    with tab_charts:
        left, right = st.columns(2)
        with left:
            try:
                fig_price = _build_plotly_figure(full.onchain)
                rr: ResearchReport | None = st.session_state.get("last_report")
                if rr and (not full.onchain.ohlcv_last_1h):
                    from tools import birdeye_price_history

                    hist = birdeye_price_history(rr.token_address, rr.chain)
                    alt = build_price_chart(hist)
                    if alt is not None:
                        fig_price = alt
                st.plotly_chart(fig_price, use_container_width=True)
            except Exception as exc:
                st.warning(f"Could not render price chart: {exc}")

        with right:
            fig_sent = build_sentiment_gauge(full.sentiment)
            if fig_sent:
                st.plotly_chart(fig_sent, use_container_width=True)
            else:
                st.info("Sentiment gauge unavailable for this run.")

        try:
            chart_uri = generate_price_chart(full.onchain, output="base64")
            if chart_uri.startswith("data:image"):
                st.image(chart_uri, caption="TokenIntel price snapshot", use_container_width=True)
        except Exception:
            pass

    with tab_raw:
        st.json(full.model_dump(mode="json"))
        if st.session_state.get("last_workflow") and st.session_state.last_workflow.raw_agent_output:
            with st.expander("Raw agent output"):
                st.text(st.session_state.last_workflow.raw_agent_output[:50000])

    with tab_sources:
        st.markdown("### Data sources")
        if full.sources:
            for src in full.sources:
                st.markdown(f"- `{src}`")
        else:
            st.caption("No sources listed for this run.")
        st.markdown("### Registered tools")
        for name in list_registered_tools():
            st.markdown(f"- `{name}`")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_init_session()
_inject_theme_css()
_cached_logging(get_settings().tintel_log_level)

_render_sidebar()
_render_hero()

query = st.text_input(
    "Enter token symbol or mint (e.g. $SOL or So111...)",
    value=st.session_state.query_input,
    placeholder="e.g. JUP, $SOL, or paste a mint address",
    help="Symbols resolve via Birdeye (name + contract). Mint addresses show the token name automatically.",
)
st.session_state.query_input = query

if query.strip():
    try:
        _pk, _pm, _ps, _pn = _parse_user_query(query, chain=st.session_state.chain)
        st.caption(f"🔎 {_token_display_label(_ps, _pn, _pm)}")
    except ValueError:
        pass

btn_col, = st.columns(1)
with btn_col:
    generate = st.button(
        "Generate Research Report",
        type="primary",
        use_container_width=True,
    )

if generate:
    _run_pipeline(query)

_render_recent_searches_panel()
_render_results()

st.markdown(
    '<footer class="tintel-footer">TokenIntel $TINTEL – Built for Swarms Agent Capital Markets Hackathon</footer>',
    unsafe_allow_html=True,
)
