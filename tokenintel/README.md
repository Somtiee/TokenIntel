# TokenIntel $TINTEL — Production-grade Web3 Research Report Generator

**TokenIntel** is a multi-agent Web3 research report generator built on the [official Swarms SDK](https://docs.swarms.world). It orchestrates specialized agents to collect on-chain and market data, analyze risk and sentiment, and produce institutional-style markdown and PDF reports through a modern Streamlit UI.

| Component | Stack |
|-----------|--------|
| Agents | Swarms `Agent` + `SequentialWorkflow` |
| UI | Streamlit |
| Models | Pydantic v2 |
| APIs | Birdeye, Helius, X (Twitter) |
| Resilience | tenacity retries, rate limiting, diskcache + `lru_cache` |
| Logging | loguru |

---

## Features

- **Multi-agent pipeline**: Data collection → on-chain analysis → social sentiment → report synthesis
- **Custom tools**: Birdeye (price/overview), Helius (Solana metadata), X search (tweepy)
- **Production patterns**: typed config, structured logging, caching, retries, env-based secrets
- **Deliverables**: Markdown reports, Plotly charts, PDF export (WeasyPrint)

---

## Quick start

### Prerequisites

- Python **3.11+**
- At least one LLM API key: `OPENAI_API_KEY`, `GROQ_API_KEY`, or `ANTHROPIC_API_KEY`
- Optional: `BIRDEYE_API_KEY`, `HELIUS_API_KEY`, `X_BEARER_TOKEN`

### Install

**Windows (Command Prompt or PowerShell):**

```powershell
cd D:\TokenIntel\tokenintel
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
copy .env.example .env
```

Then edit `.env` and add at least one LLM API key.

**macOS / Linux:**

```bash
cd tokenintel
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

> Do **not** paste comment lines (starting with `#`) into the terminal — those are documentation only.

### Run the UI

From the `tokenintel/` directory (with the venv activated):

```powershell
python -m streamlit run main.py
```

Using `python -m streamlit` avoids “streamlit is not recognized” when Scripts is not on your PATH.

Open the URL shown in the terminal (default `http://localhost:8501`).

### Programmatic use

```python
from models import ResearchRequest, ResearchDepth
from agents import run_research

request = ResearchRequest(
    token_address="YOUR_MINT_OR_CONTRACT",
    chain="solana",
    symbol_hint="EXAMPLE",
    depth=ResearchDepth.STANDARD,
)
result = run_research(request)
if result.success and result.report:
    print(result.report.full_report_markdown)
```

---

## Project layout

```
tokenintel/
├── main.py           # Streamlit UI
├── agents.py         # Swarms agents + SequentialWorkflow
├── tools.py          # Birdeye, Helius, X tools
├── utils.py          # Formatting, charts, PDF
├── models.py         # Pydantic report models
├── config.py         # Settings & env loading
├── .env.example
├── requirements.txt
└── README.md
```

---

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | One of LLM keys | OpenAI models via LiteLLM |
| `GROQ_API_KEY` | One of LLM keys | Groq models |
| `ANTHROPIC_API_KEY` | One of LLM keys | Claude models |
| `TINTEL_LLM_MODEL` | No | Default `gpt-4o` or `claude-3-5-sonnet-20241022` |
| `BIRDEYE_API_KEY` | No | Market data & charts |
| `HELIUS_API_KEY` | No | Solana on-chain metadata |
| `X_BEARER_TOKEN` | No | X API v2 recent search |
| `TINTEL_CACHE_DIR` | No | Disk cache path (default `.cache/tokenintel`) |
| `TINTEL_LOG_LEVEL` | No | loguru level (default `INFO`) |

Swarms auto-detects the configured LLM provider through LiteLLM; set `TINTEL_LLM_MODEL` to match your provider.

---

## Deployment

### Local / VPS

1. Install system dependencies for WeasyPrint (GTK/Pango on Linux; see [WeasyPrint docs](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html)).
2. Run Streamlit behind a reverse proxy (nginx) with HTTPS.
3. Use a process manager (systemd, Docker, or PM2) to keep `streamlit run main.py` alive.

Example Docker-friendly env:

```bash
export TINTEL_LOG_LEVEL=INFO
export TINTEL_CACHE_DIR=/data/cache/tokenintel
streamlit run main.py --server.port=8501 --server.address=0.0.0.0
```

### Swarms Marketplace

TokenIntel is designed to be listed on the **Swarms Marketplace**:

1. Package this repo with a clear `agent_name` and workflow description (see `agents.py`).
2. Document required tools and env vars in the marketplace listing.
3. Pin `requirements.txt` versions for reproducible installs.
4. Use `SequentialWorkflow` metadata (`name`, `description`) for discoverability.

Refer to [Swarms Marketplace documentation](https://docs.swarms.world) for submission and versioning guidelines.

### Frenzy Mode

**Frenzy Mode** (high-throughput multi-agent execution on Swarms Cloud) benefits from:

- Stateless agents (`autosave=False` in this project)
- Idempotent tools with diskcache TTL (`TINTEL_CACHE_TTL_SECONDS`)
- Rate limits aligned with upstream APIs (`TINTEL_API_RATE_LIMIT_PER_MINUTE`)

For Frenzy deployments, run multiple Streamlit/API workers behind a queue, share a Redis-backed cache (replace `diskcache` path or layer Redis in `tools.py`), and scale LLM keys per tenant.

---

## Security

- Never commit `.env` or API keys.
- Treat reports as **informational only** — not financial advice.
- Restrict network egress in production to Birdeye, Helius, X, and your LLM provider.

---

## License

Proprietary / MIT — set per your organization. Token symbol: **$TINTEL**.
