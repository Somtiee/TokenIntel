"""Report formatting, Plotly charts, PDF export, and JSON persistence for TokenIntel ($TINTEL)."""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import plotly.graph_objects as go
from loguru import logger

from models import (
    FullResearchReport,
    NewsItem,
    OnChainData,
    PriceSnapshot,
    ReportSection,
    ResearchReport,
    SentimentSummary,
    SocialSentiment,
    TweetData,
)

# Solana-inspired palette (dark mode)
_SOLANA_PURPLE = "#9945FF"
_SOLANA_GREEN = "#14F195"
_SOLANA_CYAN = "#00D1FF"
_BG = "#0B1020"
_PANEL = "#121A2B"
_TEXT = "#E6EDF7"
_MUTED = "#94A3B8"
_ACCENT = "#6366F1"

_CHART_DIR = Path(".cache/tokenintel/charts")
_REPORT_DIR = Path(".cache/tokenintel/reports")


def configure_logging(level: str = "INFO") -> None:
    """Configure loguru for CLI / Streamlit entrypoints."""
    import sys

    logger.remove()
    logger.add(
        sys.stderr,
        level=level.upper(),
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan> - "
            "<level>{message}</level>"
        ),
    )


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fmt_usd(value: float | None) -> str:
    if value is None:
        return "N/A"
    if abs(value) >= 1_000_000_000:
        return f"${value / 1_000_000_000:,.2f}B"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:,.2f}M"
    if abs(value) >= 1_000:
        return f"${value / 1_000:,.2f}K"
    return f"${value:,.6f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2f}%"


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_ohlcv_rows(onchain: OnChainData) -> pd.DataFrame:
    rows = onchain.ohlcv_last_1h or []
    if not rows:
        return pd.DataFrame()

    records: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        t = item.get("time") or item.get("timestamp")
        unix = item.get("unixTime") or item.get("unix_time")
        if t is None and unix is not None:
            try:
                ts = float(unix)
                if ts > 1e12:
                    ts /= 1000.0
                t = datetime.fromtimestamp(ts, tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                t = None
        if isinstance(t, str):
            try:
                t_norm = t.replace("Z", "+00:00")
                t = datetime.fromisoformat(t_norm)
            except ValueError:
                pass

        records.append(
            {
                "time": t,
                "open": item.get("open"),
                "high": item.get("high"),
                "low": item.get("low"),
                "close": item.get("close") or item.get("value") or item.get("price"),
                "volume": item.get("volume") or item.get("v") or item.get("vol"),
            }
        )

    df = pd.DataFrame(records)
    if df.empty:
        return df

    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
        df = df.dropna(subset=["time"]).sort_values("time")
    return df


def _build_plotly_figure(onchain: OnChainData) -> go.Figure:
    df = _parse_ohlcv_rows(onchain)
    fig = go.Figure()

    if df.empty or "close" not in df.columns or df["close"].isna().all():
        # Fallback: show current price as a single-point indicator
        price = onchain.price
        fig.add_trace(
            go.Indicator(
                mode="number+delta",
                value=price if price is not None else 0.0,
                number={"font": {"color": _SOLANA_GREEN, "size": 42}},
                delta={
                    "reference": 0,
                    "value": onchain.change_24h if onchain.change_24h is not None else 0.0,
                    "relative": False,
                    "font": {"color": _TEXT},
                },
                title={"text": "Spot Price (USD)", "font": {"color": _TEXT}},
            )
        )
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=_BG,
            plot_bgcolor=_PANEL,
            height=360,
            margin=dict(l=30, r=30, t=60, b=30),
        )
        return fig

    has_ohlc = all(c in df.columns for c in ("open", "high", "low", "close"))
    if has_ohlc and df[["open", "high", "low", "close"]].notna().all(axis=None):
        fig.add_trace(
            go.Candlestick(
                x=df["time"],
                open=df["open"],
                high=df["high"],
                low=df["low"],
                close=df["close"],
                increasing_line_color=_SOLANA_GREEN,
                decreasing_line_color="#F43F5E",
                increasing_fillcolor=_SOLANA_GREEN,
                decreasing_fillcolor="#F43F5E",
                name="OHLCV",
            )
        )
    else:
        fig.add_trace(
            go.Scatter(
                x=df["time"],
                y=df["close"],
                mode="lines",
                line=dict(color=_SOLANA_PURPLE, width=2.5),
                fill="tozeroy",
                fillcolor="rgba(153,69,255,0.18)",
                name="Price",
            )
        )

    if "volume" in df.columns and df["volume"].notna().any():
        fig.add_trace(
            go.Bar(
                x=df["time"],
                y=df["volume"],
                name="Volume",
                marker_color="rgba(20,241,149,0.35)",
                yaxis="y2",
            )
        )
        fig.update_layout(
            yaxis2=dict(
                title="Volume",
                overlaying="y",
                side="right",
                showgrid=False,
                tickfont=dict(color=_MUTED),
            )
        )

    fig.update_layout(
        title=dict(text="TokenIntel Price — Last Hour (5m)", font=dict(color=_TEXT, size=16)),
        template="plotly_dark",
        paper_bgcolor=_BG,
        plot_bgcolor=_PANEL,
        font=dict(color=_TEXT),
        xaxis=dict(
            title="Time (UTC)",
            gridcolor="rgba(148,163,184,0.15)",
            linecolor="rgba(148,163,184,0.25)",
        ),
        yaxis=dict(
            title="USD",
            gridcolor="rgba(148,163,184,0.15)",
            linecolor="rgba(148,163,184,0.25)",
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=50, r=30, t=70, b=40),
        height=420,
        hovermode="x unified",
    )
    return fig


def generate_price_chart(
    onchain: OnChainData,
    *,
    output: Literal["base64", "path"] = "base64",
    filename: str | None = None,
) -> str:
    """
    Build a professional dark-mode Plotly price chart from OHLCV data.

    Args:
        onchain: Structured on-chain snapshot including `ohlcv_last_1h`.
        output: Return mode — base64 PNG data URI or filesystem path.
        filename: Optional filename when `output="path"`.

    Returns:
        Base64 data URI string (`data:image/png;base64,...`) or absolute file path.

    Raises:
        RuntimeError: If chart rendering or export fails.
    """
    try:
        fig = _build_plotly_figure(onchain)
        _ensure_dir(_CHART_DIR)

        try:
            import kaleido  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "Plotly static export requires `kaleido`. Install with: pip install kaleido"
            ) from exc

        if output == "path":
            out_name = filename or f"price_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.png"
            out_path = _CHART_DIR / out_name
            fig.write_image(str(out_path), format="png", scale=2)
            logger.info("Price chart written path={}", out_path)
            return str(out_path.resolve())

        png_bytes = fig.to_image(format="png", scale=2)
        b64 = base64.b64encode(png_bytes).decode("ascii")
        logger.info("Price chart generated (base64) bytes={}", len(png_bytes))
        return f"data:image/png;base64,{b64}"
    except Exception as exc:
        logger.exception("generate_price_chart failed")
        raise RuntimeError(f"Failed to generate price chart: {exc}") from exc


def _report_title(report: FullResearchReport) -> str:
    sym = (report.token_symbol or "").upper().replace("$", "")
    if report.token_name and sym:
        return f"${sym} · {report.token_name}"
    if sym:
        return f"${sym}"
    return report.token_mint[:8]


def report_to_markdown(report: FullResearchReport) -> str:
    """Render a FullResearchReport into polished Markdown."""
    lines: list[str] = [
        f"# TokenIntel ($TINTEL) Research Report — {_report_title(report)}",
        "",
        f"**Token mint:** `{report.token_mint}`  ",
        f"**Generated:** {report.timestamp}  ",
        f"**Confidence:** {report.confidence:.0%}  ",
        "",
        "## Table of Contents",
        "1. [Executive Recommendation](#executive-recommendation)",
        "2. [On-Chain Snapshot](#on-chain-snapshot)",
        "3. [Sentiment Summary](#sentiment-summary)",
        "4. [Social (X/Reddit)](#social-xreddit)",
        "5. [News](#news)",
        "6. [Analysis Sections](#analysis-sections)",
        "7. [Sources](#sources)",
        "",
        "## Executive Recommendation",
        display_recommendation(report),
        "",
        "## On-Chain Snapshot",
        f"- **Price:** {_fmt_usd(report.onchain.price)}",
        f"- **Market cap:** {_fmt_usd(report.onchain.mc)}",
        f"- **Liquidity:** {_fmt_usd(report.onchain.liquidity)}",
        f"- **24h volume:** {_fmt_usd(report.onchain.volume_24h)}",
        f"- **24h change:** {_fmt_pct(report.onchain.change_24h)}",
        f"- **Holders:** {report.onchain.holders if report.onchain.holders is not None else 'N/A'}",
        f"- **Security score:** {report.onchain.security_score if report.onchain.security_score is not None else 'N/A'}",
        f"- **Token age (days):** {report.onchain.token_age_days if report.onchain.token_age_days is not None else 'N/A'}",
        "",
    ]

    if report.onchain.top_holders_summary:
        lines.append("### Top holders (summary)")
        for h in report.onchain.top_holders_summary[:10]:
            addr = h.get("address", "unknown")
            share = h.get("share_pct")
            lines.append(f"- `{addr}` — share {share if share is not None else 'N/A'}")
        lines.append("")

    lines.extend(
        [
            "## Sentiment Summary",
            f"**Overall:** {clean_prose_for_display(report.sentiment.overall, max_chars=500, mint=report.token_mint) or build_sentiment_summary_text(md_body='', score=report.sentiment.score, bullish=report.sentiment.key_bullish, bearish=report.sentiment.key_bearish)}",
            f"**Score:** {report.sentiment.score:+.2f} (-1 bearish → +1 bullish)",
            "",
        ]
    )
    if report.sentiment.key_bullish:
        lines.append("### Key bullish")
        lines.extend(f"- {b}" for b in report.sentiment.key_bullish)
        lines.append("")
    if report.sentiment.key_bearish:
        lines.append("### Key bearish")
        lines.extend(f"- {b}" for b in report.sentiment.key_bearish)
        lines.append("")

    lines.append("## Social (X/Reddit)")
    if not report.tweets:
        lines.append("_No recent tweets captured._")
    else:
        for t in report.tweets[:20]:
            author = t.author.get("username") or t.author.get("name") or "unknown"
            lines.append(f"- **@{author}** ({t.created_at}) — ❤ {t.likes} ↻ {t.retweets}")
            lines.append(f"  - {t.text}")
            if t.url:
                lines.append(f"  - {t.url}")
    lines.append("")

    lines.append("## News")
    if not report.news:
        lines.append("_No news items captured._")
    else:
        for n in report.news[:15]:
            ts = n.timestamp or "unknown time"
            url = n.url or ""
            lines.append(f"- **{n.title}** ({n.source}, {ts})")
            if url:
                lines.append(f"  - {url}")
    lines.append("")

    lines.append("## Analysis Sections")
    if not report.sections:
        lines.append("_No additional sections._")
    else:
        for section in report.sections:
            lines.extend(_section_to_markdown(section))
    lines.append("")

    lines.append("## Sources")
    if report.sources:
        lines.extend(f"- {s}" for s in report.sources)
    else:
        lines.append("- (none listed)")
    lines.append("")
    lines.append("---")
    lines.append("*TokenIntel $TINTEL — Not financial advice. DYOR.*")

    return "\n".join(lines).strip() + "\n"


def _section_to_markdown(section: ReportSection) -> list[str]:
    out = [f"### {section.title}", section.content, ""]
    if section.data:
        out.append("```json")
        out.append(json.dumps(section.data, indent=2, default=str))
        out.append("```")
        out.append("")
    return out


def _markdown_to_html(markdown_text: str) -> str:
    """Convert Markdown to HTML using `markdown` if available, else minimal fallback."""
    try:
        import markdown as md  # type: ignore[import-untyped]

        body = md.markdown(
            markdown_text,
            extensions=["extra", "sane_lists", "tables", "toc"],
        )
    except Exception as exc:
        logger.warning("markdown package unavailable; using fallback renderer err={}", exc)
        body = _fallback_markdown_to_html(markdown_text)

    toc_match = re.search(r"<h2[^>]*>Table of Contents</h2>(.*?)<h2", body, flags=re.DOTALL | re.IGNORECASE)
    toc_html = ""
    if toc_match:
        toc_html = f'<nav class="toc"><h2>Table of Contents</h2>{toc_match.group(1)}</nav>'

    return _wrap_html_document(body_html=body, toc_html=toc_html)


def _fallback_markdown_to_html(md: str) -> str:
    html = md
    html = re.sub(r"^# (.+)$", r"<h1>\1</h1>", html, flags=re.MULTILINE)
    html = re.sub(r"^## (.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
    html = re.sub(r"^### (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"`([^`]+)`", r"<code>\1</code>", html)
    html = re.sub(r"^\- (.+)$", r"<li>\1</li>", html, flags=re.MULTILINE)
    html = re.sub(r"(<li>.*?</li>\s*)+", lambda m: f"<ul>{m.group(0)}</ul>", html, flags=re.DOTALL)
    parts = [f"<p>{p.strip()}</p>" for p in html.split("\n\n") if p.strip() and not p.startswith("<")]
    return "\n".join(parts) if parts else html


def _wrap_html_document(*, body_html: str, toc_html: str) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>TokenIntel ($TINTEL) Research Report</title>
  <style>
    @page {{
      size: A4;
      margin: 18mm 16mm 22mm 16mm;
      @bottom-center {{
        content: "TokenIntel $TINTEL • {generated} • Not financial advice";
        font-size: 9px;
        color: #64748b;
      }}
    }}
    body {{
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      color: #0f172a;
      line-height: 1.55;
      font-size: 11.5pt;
    }}
    .brand-header {{
      background: linear-gradient(135deg, #0f172a 0%, #312e81 55%, #9945FF 120%);
      color: #f8fafc;
      padding: 18px 20px;
      border-radius: 12px;
      margin-bottom: 18px;
    }}
    .brand-header h1 {{
      margin: 0;
      font-size: 22px;
      letter-spacing: 0.2px;
    }}
    .brand-header p {{
      margin: 6px 0 0 0;
      color: #c7d2fe;
      font-size: 12px;
    }}
    .toc {{
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-left: 4px solid #9945FF;
      padding: 12px 14px;
      border-radius: 10px;
      margin: 14px 0 20px 0;
      page-break-inside: avoid;
    }}
    .toc h2 {{ margin-top: 0; font-size: 14px; color: #312e81; }}
    .toc ul {{ margin: 0; padding-left: 18px; }}
    h1 {{ color: #312e81; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px; }}
    h2 {{ color: #1e293b; margin-top: 22px; page-break-after: avoid; }}
    h3 {{ color: #334155; margin-top: 16px; }}
    code {{
      background: #f1f5f9;
      padding: 1px 5px;
      border-radius: 4px;
      font-size: 0.92em;
      word-break: break-all;
    }}
    pre {{
      background: #0b1020;
      color: #e2e8f0;
      padding: 10px 12px;
      border-radius: 8px;
      overflow: hidden;
      font-size: 9.5pt;
    }}
    ul {{ padding-left: 18px; }}
    .metric-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin: 12px 0 16px 0;
    }}
    .metric-card {{
      border: 1px solid #e2e8f0;
      border-radius: 10px;
      padding: 10px 12px;
      background: #ffffff;
    }}
    .metric-label {{ color: #64748b; font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; }}
    .metric-value {{ font-size: 14px; font-weight: 700; color: #0f172a; }}
    .chart {{
      width: 100%;
      margin: 14px 0 18px 0;
      border: 1px solid #e2e8f0;
      border-radius: 10px;
      overflow: hidden;
      page-break-inside: avoid;
    }}
    .footer-note {{
      margin-top: 24px;
      padding-top: 10px;
      border-top: 1px solid #e2e8f0;
      color: #64748b;
      font-size: 10px;
    }}
  </style>
</head>
<body>
  <div class="brand-header">
    <h1>TokenIntel Research Report</h1>
    <p>Production-grade Web3 intelligence • $TINTEL</p>
  </div>
  {toc_html}
  {body_html}
  <div class="footer-note">
    TokenIntel $TINTEL — Generated {generated}. This report is informational only and not financial advice.
  </div>
</body>
</html>"""


def _inject_chart_and_metrics(html: str, report: FullResearchReport, chart_src: str | None) -> str:
    metrics_html = f"""
    <div class="metric-grid">
      <div class="metric-card"><div class="metric-label">Price</div><div class="metric-value">{_fmt_usd(report.onchain.price)}</div></div>
      <div class="metric-card"><div class="metric-label">Market Cap</div><div class="metric-value">{_fmt_usd(report.onchain.mc)}</div></div>
      <div class="metric-card"><div class="metric-label">Liquidity</div><div class="metric-value">{_fmt_usd(report.onchain.liquidity)}</div></div>
      <div class="metric-card"><div class="metric-label">24h Change</div><div class="metric-value">{_fmt_pct(report.onchain.change_24h)}</div></div>
    </div>
    """
    chart_html = ""
    if chart_src:
        chart_html = f'<div class="chart"><img src="{chart_src}" alt="TokenIntel price chart" style="width:100%;height:auto;" /></div>'

    # Insert after first h2 if possible, else at top of body content.
    marker = "<h2"
    idx = html.find(marker)
    if idx == -1:
        return metrics_html + chart_html + html
    return html[:idx] + metrics_html + chart_html + html[idx:]


def report_to_pdf(report: FullResearchReport, output_path: str = "report.pdf") -> str:
    """
    Render FullResearchReport to a branded PDF via Markdown → HTML → WeasyPrint.

    Args:
        report: Validated research report model.
        output_path: Destination PDF path.

    Returns:
        Absolute path to the written PDF.

    Raises:
        RuntimeError: If PDF generation fails.
    """
    out = Path(output_path)
    _ensure_dir(out.parent if out.parent != Path(".") else _REPORT_DIR)

    try:
        md = report_to_markdown(report)
        html = _markdown_to_html(md)

        chart_src: str | None = None
        try:
            chart_data_uri = generate_price_chart(report.onchain, output="base64")
            chart_src = chart_data_uri
        except Exception as exc:
            logger.warning("Skipping chart embed in PDF err={}", exc)

        html = _inject_chart_and_metrics(html, report, chart_src)

        from weasyprint import HTML

        HTML(string=html, base_url=str(Path.cwd())).write_pdf(str(out))
        logger.info("PDF report written path={}", out.resolve())
        return str(out.resolve())
    except Exception as exc:
        logger.exception("report_to_pdf failed path={}", output_path)
        raise RuntimeError(f"Failed to generate PDF report: {exc}") from exc


def save_report_json(report: FullResearchReport, filename: str) -> str:
    """
    Persist a FullResearchReport as JSON.

    Args:
        report: Validated report.
        filename: Output filename or path.

    Returns:
        Absolute path to saved JSON file.

    Raises:
        RuntimeError: If serialization or write fails.
    """
    path = Path(filename)
    _ensure_dir(path.parent if path.parent != Path(".") else _REPORT_DIR)

    try:
        payload = report.model_dump(mode="json")
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        logger.info("Report JSON saved path={}", path.resolve())
        return str(path.resolve())
    except Exception as exc:
        logger.exception("save_report_json failed filename={}", filename)
        raise RuntimeError(f"Failed to save report JSON: {exc}") from exc


# Optional helpers for Streamlit / agents integration


def tweets_to_display_lines(tweets: list[TweetData], limit: int = 10) -> list[str]:
    lines: list[str] = []
    for t in tweets[:limit]:
        user = t.author.get("username") or t.author.get("name") or "unknown"
        lines.append(f"@{user}: {t.text[:180]}")
    return lines


def news_to_display_lines(news: list[NewsItem], limit: int = 10) -> list[str]:
    return [f"{n.source}: {n.title}" for n in news[:limit]]


# ---------------------------------------------------------------------------
# Swarms output parsing (conversation JSON → readable markdown)
# ---------------------------------------------------------------------------

_AGENT_MARKERS: tuple[str, ...] = (
    "Produce a TokenIntel research run",
    "TokenIntel-DataCollector",
    "TokenIntel-OnChainAnalyst",
    "TokenIntel-SentimentAnalyst",
    "TokenIntel-ReportWriter",
    "Agent behind",
    "Sequential awareness",
    "'type': 'function'",
    '"type": "function"',
    "birdeye_token_overview",
    "helius_token_metadata",
    "x_token_sentiment",
    "Symbol hint:",
    "Depth: standard",
    "On-chain analysis:",
)

_MINT_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
_SCHEMA_MARKERS = (
    "default:",
    "description:",
    "required:",
    "properties:",
    "anyof",
    "max_result",
    "max_results",
)


def _short_mint(mint: str) -> str:
    mint = (mint or "").strip()
    if len(mint) <= 14:
        return mint
    return f"{mint[:6]}…{mint[-4:]}"


def _looks_like_agent_dump(text: str) -> bool:
    s = (text or "").strip()
    if len(s) < 30:
        return False
    if s.startswith("[") and '"role"' in s[:500]:
        return True
    if s.startswith("{") and '"role"' in s[:300]:
        return True
    return False


def is_readable_prose(text: str, *, min_len: int = 60) -> bool:
    """True when text looks like human report prose (not agent logs / JSON)."""
    if not text:
        return False
    s = sanitize_prose(text).strip()
    if len(s) < min_len:
        return False
    lower = s.lower()
    if any(marker.lower() in lower for marker in _AGENT_MARKERS):
        return False
    marker_hits = sum(1 for marker in _SCHEMA_MARKERS if marker in lower)
    if marker_hits >= 2:
        return False
    if re.search(r"\[\s*\{", s) or re.search(r"\{\s*['\"]role['\"]", s):
        return False
    if s.count("{") >= 2 and s.count("}") >= 2:
        return False
    if re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{20,}", s.replace(" ", "")):
        return False
    words = re.findall(r"[A-Za-z]{3,}", s)
    return len(words) >= 8


def sanitize_prose(text: str, *, mint: str | None = None) -> str:
    """Remove agent logs, JSON blobs, and shorten embedded mint addresses."""
    if not text:
        return ""
    out = text
    if _looks_like_agent_dump(out):
        extracted = extract_workflow_markdown(out)
        if extracted and extracted != out:
            out = extracted
    out = re.sub(r"```[\s\S]*?```", " ", out)
    out = re.sub(r"\[\s*\{[\s\S]*?\}\s*\]", " ", out)
    lines: list[str] = []
    for line in out.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(marker.lower() in stripped.lower() for marker in _AGENT_MARKERS):
            continue
        if re.match(r"^(User|Assistant|System)\s*:", stripped, re.IGNORECASE):
            continue
        if re.match(r"^TokenIntel-\w+\s*:", stripped):
            continue
        if "'type'" in stripped and "function" in stripped:
            continue
        low = stripped.lower()
        if sum(1 for marker in _SCHEMA_MARKERS if marker in low) >= 2:
            continue
        lines.append(stripped)
    out = " ".join(lines)
    out = _MINT_RE.sub(lambda m: _short_mint(m.group(0)), out)
    if mint:
        out = out.replace(mint, _short_mint(mint))
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    parts.append(str(part.get("text", "")))
                elif "text" in part:
                    parts.append(str(part["text"]))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(p for p in parts if p).strip()
    return str(content).strip()


def extract_workflow_markdown(raw_output: str) -> str:
    """
    Convert Swarms workflow output (often a JSON message list) into report markdown.
    """
    text = (raw_output or "").strip()
    if not text:
        return ""

    if not _looks_like_agent_dump(text):
        return text

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text

    messages: list[Any] = []
    if isinstance(parsed, list):
        messages = parsed
    elif isinstance(parsed, dict):
        messages = parsed.get("messages") or parsed.get("history") or [parsed]

    candidates: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "")).lower()
        content = _message_content(msg.get("content"))
        if not content or len(content) < 20:
            continue
        if role in ("user",):
            continue
        if _looks_like_agent_dump(content):
            nested = extract_workflow_markdown(content)
            if nested and is_readable_prose(nested, min_len=40):
                candidates.append(nested)
            continue
        if is_readable_prose(content, min_len=40):
            candidates.append(content)

    for c in reversed(candidates):
        if re.search(r"##\s*(Executive Summary|Verdict)", c, re.IGNORECASE):
            return c
    for c in reversed(candidates):
        if is_readable_prose(c, min_len=80):
            return c
    if candidates:
        return candidates[-1]
    return text


def extract_markdown_section(markdown: str, *section_titles: str) -> str:
    """Pull prose from a ## Section heading until the next ## heading."""
    body = extract_workflow_markdown(markdown) if _looks_like_agent_dump(markdown) else markdown
    for title in section_titles:
        pattern = rf"##\s*{re.escape(title)}[^\n]*\n(.*?)(?=\n##\s|\Z)"
        match = re.search(pattern, body, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        section = sanitize_prose(match.group(1).strip())
        if section and is_readable_prose(section, min_len=40):
            return section
    return ""


def compose_executive_recommendation(
    *,
    symbol: str,
    risk_level: Any,
    price: PriceSnapshot | None,
    bullish: list[str],
    bearish: list[str],
    confidence: float,
) -> str:
    """Deterministic, readable executive summary when LLM output is unusable."""
    sym = (symbol or "TOKEN").upper().replace("$", "")
    paragraphs: list[str] = []

    if price and price.price_usd is not None:
        ch = price.change_24h_pct
        ch_txt = f"{ch:+.2f}% over 24h" if ch is not None else "24h change unavailable"
        paragraphs.append(
            f"${sym} trades at {_fmt_usd(price.price_usd)} ({ch_txt}). "
            f"Liquidity sits at {_fmt_usd(price.liquidity_usd)} with "
            f"{_fmt_usd(price.volume_24h_usd)} in 24h volume"
            + (f" and market cap near {_fmt_usd(price.market_cap_usd)}." if price.market_cap_usd else ".")
        )
    else:
        paragraphs.append(
            f"TokenIntel completed a standard-depth review of ${sym}. "
            "Live market metrics were limited, so treat sizing and entries conservatively."
        )

    risk_val = getattr(risk_level, "value", str(risk_level or "unknown")).upper()
    paragraphs.append(
        f"Risk is rated {risk_val} with {confidence:.0%} model confidence based on on-chain, "
        "liquidity, and narrative signals gathered in this run."
    )

    clean_bull = [sanitize_prose(b) for b in bullish if is_readable_prose(b, min_len=12)][:3]
    clean_bear = [sanitize_prose(b) for b in bearish if is_readable_prose(b, min_len=12)][:3]
    if clean_bull:
        paragraphs.append("Upside drivers: " + "; ".join(clean_bull).rstrip(".") + ".")
    if clean_bear:
        paragraphs.append("Key risks: " + "; ".join(clean_bear).rstrip(".") + ".")

    if risk_val in ("HIGH", "CRITICAL"):
        paragraphs.append(
            "Recommendation: stay cautious—reduce size, demand clear liquidity depth, "
            "and wait for holder concentration or narrative to improve before adding exposure."
        )
    elif risk_val == "LOW":
        paragraphs.append(
            "Recommendation: constructive for monitored accumulation—confirm your catalyst timeline, "
            "use defined risk, and scale in only while liquidity remains stable."
        )
    else:
        paragraphs.append(
            "Recommendation: neutral-to-cautiously constructive—appropriate for watchlists and "
            "small pilot positions after you validate thesis, unlocks, and exit liquidity."
        )

    return "\n\n".join(paragraphs)


def build_executive_recommendation(
    *,
    symbol: str,
    mint: str,
    risk_level: Any,
    price: PriceSnapshot | None,
    bullish: list[str],
    bearish: list[str],
    confidence: float,
    md_body: str = "",
    legacy_executive: str = "",
) -> str:
    """Pick the best executive write-up, else compose one from structured data."""
    for title in ("Executive Summary", "Verdict", "Market Snapshot"):
        section = extract_markdown_section(md_body, title)
        if is_readable_prose(section, min_len=80):
            return section[:1400]

    body = extract_workflow_markdown(md_body) if _looks_like_agent_dump(md_body) else md_body
    for para in re.split(r"\n\n+", body or ""):
        cleaned = sanitize_prose(para, mint=mint)
        if is_readable_prose(cleaned, min_len=100):
            return cleaned[:1400]

    legacy = sanitize_prose(legacy_executive, mint=mint)
    if is_readable_prose(legacy, min_len=80):
        return legacy[:1400]

    return compose_executive_recommendation(
        symbol=symbol,
        risk_level=risk_level,
        price=price,
        bullish=bullish,
        bearish=bearish,
        confidence=confidence,
    )


def build_sentiment_summary_text(
    *,
    md_body: str,
    score: float,
    bullish: list[str],
    bearish: list[str],
) -> str:
    section = extract_markdown_section(md_body, "Social Sentiment", "Sentiment")
    if is_readable_prose(section, min_len=40):
        return sanitize_prose(section)[:400]

    if score >= 0.25:
        tone = "leaning bullish"
    elif score <= -0.25:
        tone = "leaning bearish"
    else:
        tone = "mixed / neutral"

    hints: list[str] = []
    for item in bullish[:2] + bearish[:2]:
        cleaned = sanitize_prose(item)
        if is_readable_prose(cleaned, min_len=12):
            hints.append(cleaned.rstrip("."))
    if hints:
        return f"Social tone is {tone} ({score:+.2f}). Highlights: " + "; ".join(hints) + "."
    return f"Social tone is {tone} with a sentiment score of {score:+.2f}."


def display_recommendation(report: FullResearchReport) -> str:
    """Always-safe recommendation string for UI, cards, and share text."""
    rec = sanitize_prose(report.recommendation, mint=report.token_mint)
    if is_readable_prose(rec, min_len=80):
        return rec[:1400]
    return compose_executive_recommendation(
        symbol=report.token_symbol,
        risk_level="medium",
        price=PriceSnapshot(
            price_usd=report.onchain.price,
            change_24h_pct=report.onchain.change_24h,
            volume_24h_usd=report.onchain.volume_24h,
            market_cap_usd=report.onchain.mc,
            liquidity_usd=report.onchain.liquidity,
        ),
        bullish=report.sentiment.key_bullish,
        bearish=report.sentiment.key_bearish,
        confidence=report.confidence,
    )


def share_snippet_for_x(report: FullResearchReport, max_chars: int = 240) -> str:
    """Short tweet-safe blurb without JSON or full mint addresses."""
    sym = (report.token_symbol or "TOKEN").upper().replace("$", "")
    rec = display_recommendation(report)
    first_sentence = re.split(r"(?<=[.!?])\s+", rec, maxsplit=1)[0].strip()
    if len(first_sentence) > max_chars - 80:
        first_sentence = first_sentence[: max_chars - 83].rsplit(" ", 1)[0] + "..."
    price = report.onchain.price
    price_txt = _fmt_usd(price) if price is not None else "N/A"
    ch = report.onchain.change_24h
    ch_txt = f"{ch:+.1f}%" if ch is not None else "—"
    return (
        f"TokenIntel $TINTEL — ${sym} @ {price_txt} ({ch_txt} 24h). "
        f"Sentiment {report.sentiment.score:+.2f}. {first_sentence}"
    )[:280]


def clean_prose_for_display(text: str, max_chars: int = 900, *, mint: str | None = None) -> str:
    """Strip markdown noise and agent dumps for UI cards."""
    if not text:
        return ""
    text = sanitize_prose(text, mint=mint)
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not is_readable_prose(text, min_len=30):
        return ""
    if len(text) > max_chars:
        text = text[: max_chars - 3].rsplit(" ", 1)[0] + "..."
    return text


def _tokenize_for_wrap(text: str) -> list[str]:
    """Split text into wrap-friendly tokens (breaks long base58 strings)."""
    tokens: list[str] = []
    for word in text.split():
        if len(word) > 18 and re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]+", word):
            tokens.append(_short_mint(word))
        elif len(word) > 22:
            while len(word) > 22:
                tokens.append(word[:22] + "-")
                word = word[22:]
            if word:
                tokens.append(word)
        else:
            tokens.append(word)
    return tokens


def _wrap_text_lines(draw: Any, text: str, font: Any, max_width: int) -> list[str]:
    words = _tokenize_for_wrap(text)
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}".replace(" -", "")
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current.rstrip("-"))
            current = word
    lines.append(current.rstrip("-"))
    return lines


def report_to_share_card(report: FullResearchReport) -> bytes:
    """
    Render a branded 1200×675 JPEG share card (Windows-friendly, no GTK).
    """
    from io import BytesIO

    from PIL import Image, ImageDraw, ImageFont

    w, h = 1200, 675
    img = Image.new("RGB", (w, h), _BG)
    draw = ImageDraw.Draw(img)

    for y in range(h):
        t = y / h
        r = int(11 + (49 - 11) * t)
        g = int(16 + (46 - 16) * t)
        b = int(32 + (129 - 32) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))

    def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        # Try common font-family names first (works on many Linux builds without absolute paths).
        names = (
            ["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Arial Bold.ttf"]
            if bold
            else ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"]
        )
        for name in names:
            try:
                return ImageFont.truetype(name, size)
            except Exception:
                pass
        try:
            pil_fonts = Path(ImageFont.__file__).resolve().parent / "Fonts"
            bundled = pil_fonts / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
            if bundled.exists():
                return ImageFont.truetype(str(bundled), size)
        except Exception:
            pass
        candidates: list[Path] = []
        if bold:
            candidates.extend(
                [
                    Path("C:/Windows/Fonts/segoeuib.ttf"),
                    Path("C:/Windows/Fonts/arialbd.ttf"),
                    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
                    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
                ]
            )
        else:
            candidates.extend(
                [
                    Path("C:/Windows/Fonts/segoeui.ttf"),
                    Path("C:/Windows/Fonts/arial.ttf"),
                    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
                ]
            )
        for path in candidates:
            if path.exists():
                return ImageFont.truetype(str(path), size)
        # Pillow 10+ supports scalable default font via size=.
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    title_font = _font(52, bold=True)
    sub_font = _font(28)
    body_font = _font(24)
    small_font = _font(20)

    sym = (report.token_symbol or "TOKEN").upper().replace("$", "")
    draw.rounded_rectangle((40, 40, w - 40, h - 40), radius=28, fill=_PANEL, outline=_SOLANA_PURPLE, width=3)
    draw.text((72, 68), "TokenIntel", font=sub_font, fill=_MUTED)
    draw.text((72, 108), f"${sym}", font=title_font, fill=_SOLANA_GREEN)
    if report.token_name:
        draw.text((72, 168), report.token_name[:48], font=body_font, fill=_TEXT)
        metrics_y = 210
    else:
        metrics_y = 188

    price = report.onchain.price
    metrics = (
        f"Price {_fmt_usd(price)}  ·  24h {_fmt_pct(report.onchain.change_24h)}  ·  "
        f"Liq {_fmt_usd(report.onchain.liquidity)}  ·  Conf {report.confidence:.0%}"
    )
    draw.text((72, metrics_y), metrics, font=small_font, fill=_TEXT)

    rec = clean_prose_for_display(
        display_recommendation(report),
        max_chars=380,
        mint=report.token_mint,
    )
    if not rec:
        rec = "See the full TokenIntel report for the complete thesis and risk factors."
    rec_y = metrics_y + 52
    draw.text((72, rec_y), "Executive recommendation", font=sub_font, fill=_SOLANA_PURPLE)
    y = rec_y + 44
    for line in _wrap_text_lines(draw, rec, body_font, w - 144)[:5]:
        draw.text((72, y), line, font=body_font, fill=_TEXT)
        y += 34

    sent_line = (
        clean_prose_for_display(report.sentiment.overall, max_chars=90, mint=report.token_mint)
        or f"{'Bullish' if report.sentiment.score > 0.2 else 'Bearish' if report.sentiment.score < -0.2 else 'Neutral'} social tone"
    )
    draw.text(
        (72, h - 120),
        f"Sentiment {report.sentiment.score:+.2f} · {sent_line}",
        font=small_font,
        fill=_MUTED,
    )
    mint_short = _short_mint(report.token_mint)
    draw.text((72, h - 82), f"Mint {mint_short} · Not financial advice", font=small_font, fill=_MUTED)
    draw.text((w - 280, h - 82), "TokenIntel $TINTEL", font=small_font, fill=_SOLANA_CYAN)

    buf = BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=92, optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Legacy compatibility (main.py + agents.py ResearchReport pipeline)
# ---------------------------------------------------------------------------


def workflow_result_to_report(
    token_address: str,
    chain: str,
    raw_output: str,
    symbol_hint: str | None = None,
) -> ResearchReport:
    """Map Swarms markdown output into ResearchReport with safe defaults."""
    from models import ResearchReport, RiskLevel

    markdown_body = extract_workflow_markdown(raw_output)
    risk = RiskLevel.UNKNOWN
    lower = markdown_body.lower()
    if "critical risk" in lower:
        risk = RiskLevel.CRITICAL
    elif "high risk" in lower:
        risk = RiskLevel.HIGH
    elif "medium risk" in lower or "moderate risk" in lower:
        risk = RiskLevel.MEDIUM
    elif "low risk" in lower:
        risk = RiskLevel.LOW

    def _bullets(keyword: str) -> list[str]:
        pattern = rf"##\s*.*{keyword}.*\n(.*?)(?=\n## |\Z)"
        match = re.search(pattern, markdown_body, re.IGNORECASE | re.DOTALL)
        if not match:
            return []
        section = match.group(1)
        return [ln.lstrip("-•* ").strip() for ln in section.splitlines() if ln.strip().startswith(("-", "•", "*"))]

    confidence = 0.65
    if risk == RiskLevel.LOW:
        confidence = 0.78
    elif risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        confidence = 0.55

    executive = build_executive_recommendation(
        symbol=symbol_hint or token_address[:6],
        mint=token_address,
        risk_level=risk,
        price=None,
        bullish=_bullets("Bullish"),
        bearish=_bullets("Bearish"),
        confidence=confidence,
        md_body=markdown_body,
        legacy_executive=extract_markdown_section(markdown_body, "Executive Summary", "Verdict"),
    )

    return ResearchReport(
        token_address=token_address,
        chain=chain,
        symbol=symbol_hint,
        executive_summary=executive,
        full_report_markdown=markdown_body,
        risk_level=risk,
        risk_factors=_bullets("Risk"),
        bullish_thesis=_bullets("Bullish"),
        bearish_thesis=_bullets("Bearish"),
    )


def parse_price_from_tool_json(raw: str | dict[str, Any]) -> PriceSnapshot | None:
    """Best-effort Birdeye overview → PriceSnapshot."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return None
    inner = data.get("data", data)
    if not isinstance(inner, dict):
        return None
    return PriceSnapshot(
        price_usd=_safe_float(inner.get("price")),
        change_24h_pct=_safe_float(inner.get("priceChange24hPercent")),
        volume_24h_usd=_safe_float(inner.get("v24hUSD")),
        market_cap_usd=_safe_float(inner.get("mc")),
        liquidity_usd=_safe_float(inner.get("liquidity")),
    )


def compute_sentiment_score(
    *,
    social: SocialSentiment | None = None,
    social_json: str | None = None,
    md_body: str = "",
    price_change_24h: float | None = None,
) -> float:
    """Derive sentiment in [-1, 1] from social data, markdown, or price momentum."""
    if social and social.sentiment_score is not None:
        score = max(-1.0, min(1.0, float(social.sentiment_score)))
        mentions = int(getattr(social, "mention_count", 0) or 0)
        # Avoid hard-locking to 0.00 when there are too few social samples.
        if not (abs(score) < 0.01 and mentions < 3):
            return score

    if social_json:
        try:
            data = json.loads(social_json)
            if data.get("sentiment_score") is not None:
                return max(-1.0, min(1.0, float(data["sentiment_score"])))
            posts = data.get("posts") or data.get("tweets") or []
            from tools import score_texts_sentiment

            texts = [str(p.get("text", "")) for p in posts if isinstance(p, dict)]
            lexical = score_texts_sentiment(texts)
            if lexical != 0.0:
                return lexical
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    section = extract_markdown_section(md_body, "Social Sentiment", "Sentiment").lower()
    if "bullish" in section and "bearish" not in section:
        return 0.35
    if "bearish" in section and "bullish" not in section:
        return -0.35

    if price_change_24h is not None:
        score = max(-1.0, min(1.0, float(price_change_24h) / 20.0))
    else:
        score = 0.0

    # Avoid displaying noisy "-0.00" / "+0.00" due float precision.
    if abs(score) < 0.015:
        return 0.0
    return score


def enrich_report_from_tools(
    report: ResearchReport,
    birdeye_overview_json: str | None = None,
    social_json: str | None = None,
    news_json: str | None = None,
) -> ResearchReport:
    """Attach structured snapshots parsed from tool outputs."""
    if birdeye_overview_json:
        report.price = parse_price_from_tool_json(birdeye_overview_json)
        try:
            from tools import lookup_token_identity

            meta = lookup_token_identity(report.token_address, report.chain)
            if meta.get("symbol") and not report.symbol:
                report.symbol = meta["symbol"]
            if meta.get("name") and not report.name:
                report.name = meta["name"]
        except Exception:
            pass
    if social_json:
        try:
            data = json.loads(social_json)
            posts = data.get("posts") or data.get("tweets") or []
            texts = [str(p.get("text", "")) for p in posts if isinstance(p, dict) and p.get("text")]
            from tools import score_texts_sentiment

            score = data.get("sentiment_score")
            if score is None:
                score = score_texts_sentiment(texts)
            report.social = SocialSentiment(
                mention_count=int(data.get("count", len(posts))),
                sentiment_score=max(-1.0, min(1.0, float(score))) if score is not None else None,
                sample_tweets=texts[:5],
                source=",".join(data.get("sources", ["social"])),
            )
            # Keep raw social posts so UI can render Reddit snippets when X has no tweets.
            report.metadata["social_posts"] = posts[:20]
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning("Could not parse social_json for enrichment")
    if news_json:
        try:
            data = json.loads(news_json)
            report.metadata["news"] = data.get("news", [])
        except json.JSONDecodeError:
            logger.warning("Could not parse news_json for enrichment")
    report.full_report_markdown = format_markdown_report(report)
    return report


def format_markdown_report(report: ResearchReport) -> str:
    """Build markdown for legacy ResearchReport (Streamlit UI)."""
    lines: list[str] = [
        f"# TokenIntel Research Report — {report.symbol or report.token_address[:8]}",
        "",
        f"**Chain:** {report.chain}  ",
        f"**Address:** `{report.token_address}`  ",
        f"**Generated:** {report.generated_at.isoformat()}  ",
        f"**Risk:** {report.risk_level.value.upper()}",
        "",
        "## Executive Summary",
        report.executive_summary,
        "",
    ]
    if report.price:
        p = report.price
        lines.extend(
            [
                "## Market Snapshot",
                f"- **Price (USD):** {p.price_usd}",
                f"- **24h Change:** {p.change_24h_pct}%",
                f"- **24h Volume:** {_fmt_usd(p.volume_24h_usd)}",
                f"- **Market Cap:** {_fmt_usd(p.market_cap_usd)}",
                f"- **Liquidity:** {_fmt_usd(p.liquidity_usd)}",
                "",
            ]
        )
    if report.bullish_thesis:
        lines.append("## Bullish Thesis")
        lines.extend(f"- {x}" for x in report.bullish_thesis)
        lines.append("")
    if report.bearish_thesis:
        lines.append("## Bearish Thesis")
        lines.extend(f"- {x}" for x in report.bearish_thesis)
        lines.append("")
    if report.risk_factors:
        lines.append("## Risk Factors")
        lines.extend(f"- {x}" for x in report.risk_factors)
        lines.append("")
    for section in report.sections:
        lines.extend([f"## {section.title}", section.content_markdown, ""])
    return "\n".join(lines).strip() + "\n"


def build_price_chart(price_history_json: str) -> go.Figure | None:
    """Legacy chart builder from Birdeye history JSON (Streamlit tab)."""
    try:
        payload = json.loads(price_history_json)
    except json.JSONDecodeError:
        return None
    items = payload.get("data", {}).get("items") or payload.get("items") or []
    if not items:
        return None
    df = pd.DataFrame(items)
    if df.empty:
        return None
    x_col = "unixTime" if "unixTime" in df.columns else df.columns[0]
    y_col = "value" if "value" in df.columns else "price" if "price" in df.columns else df.columns[-1]
    if x_col == "unixTime":
        df["time"] = pd.to_datetime(df["unixTime"], unit="s", utc=True)
        x_series = df["time"]
    else:
        x_series = df[x_col]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=x_series,
            y=df[y_col],
            mode="lines",
            line=dict(color=_SOLANA_PURPLE, width=2.5),
            fill="tozeroy",
            fillcolor="rgba(153,69,255,0.18)",
            name="Price",
        )
    )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=_BG,
        plot_bgcolor=_PANEL,
        font=dict(color=_TEXT),
        height=360,
        title="Price History",
        xaxis_title="Time (UTC)",
        yaxis_title="USD",
    )
    return fig


def build_sentiment_gauge(sentiment: SentimentSummary | SocialSentiment | None) -> go.Figure | None:
    """Plotly gauge; accepts FullResearchReport sentiment or legacy SocialSentiment."""
    if isinstance(sentiment, SocialSentiment):
        if sentiment.sentiment_score is None:
            return None
        sentiment = SentimentSummary(
            overall="Social sentiment",
            score=float(sentiment.sentiment_score),
            key_bullish=[],
            key_bearish=[],
        )
    if sentiment is None:
        return None
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=sentiment.score,
            title={"text": "Social Sentiment", "font": {"color": _TEXT}},
            gauge={
                "axis": {"range": [-1, 1]},
                "bar": {"color": _SOLANA_PURPLE},
                "steps": [
                    {"range": [-1, -0.3], "color": "#F43F5E"},
                    {"range": [-0.3, 0.3], "color": "#64748B"},
                    {"range": [0.3, 1], "color": _SOLANA_GREEN},
                ],
            },
            number={"font": {"color": _TEXT}},
        )
    )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=_BG,
        plot_bgcolor=_PANEL,
        height=300,
        margin=dict(l=20, r=20, t=50, b=20),
    )
    return fig


def export_pdf(report: ResearchReport, output_path: Path | None = None) -> bytes:
    """Legacy PDF export for ResearchReport; returns bytes for Streamlit download."""
    md = report.full_report_markdown or format_markdown_report(report)
    html = _markdown_to_html(md)
    try:
        from weasyprint import HTML

        pdf_bytes = HTML(string=html).write_pdf()
    except Exception as exc:
        logger.error("PDF export failed: {}", exc)
        raise RuntimeError(f"PDF export failed: {exc}") from exc
    if output_path is not None:
        output_path.write_bytes(pdf_bytes)
    return pdf_bytes
