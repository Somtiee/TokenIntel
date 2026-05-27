# TokenIntel ($TINTEL) — Production-grade Web3 Research Report Generator

TokenIntel is a multi-agent Web3 research report generator built on the official Swarms SDK. It orchestrates on-chain and market-data tools (Birdeye + Helius), social/news signals, and produces a polished branded report through a modern Streamlit UI.

## What you get

- Multi-agent workflow (Swarms `Agent` + `SequentialWorkflow`)
- Custom tools: Birdeye token overview, Helius Solana metadata, and social/news signals
- Streamlit UI with exportable deliverables (Markdown + charts + PDF in supported environments)
- Production patterns: typed config, structured logging, rate limiting, disk caching

---

## 1) How to run locally

### 1.1 Prerequisites

- Windows 10/11 (or a compatible environment)
- Python 3.11+
- At least one LLM provider key:
  - `OPENAI_API_KEY`, `GROQ_API_KEY`, or `ANTHROPIC_API_KEY`
- Recommended (for best results):
  - `BIRDEYE_API_KEY` (price/overview/search)
  - `HELIUS_API_KEY` (Solana on-chain metadata)
- Optional:
  - `X_BEARER_TOKEN` (only if you want X tweets; Reddit/RSS works without it)

### 1.2 Install and start (Windows)

From the Streamlit UI directory (`tokenintel/`):

```powershell
cd /d D:\TokenIntel\tokenintel
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
copy .env.example .env
notepad .env
```

Then edit `.env` and set at least one LLM provider key. Add `BIRDEYE_API_KEY` and `HELIUS_API_KEY` if you want on-chain/market metrics.

Run the UI:

```powershell
python -m streamlit run main.py
```

Open the URL shown in the terminal (default: `http://localhost:8501`).

### 1.3 How to test report generation

Recommended: use the one-click helper at the repo root:

```powershell
cd /d D:\TokenIntel
python .\launch_helper.py
```

This will:
- Run a local test report on `$SOL`
- Package the Swarms agent code into a single uploadable payload
- Print the Frenzy Mode URL and the Frenzy tokenization command

Manual test (programmatic):

```python
from models import ResearchRequest, ResearchDepth
from agents import run_research

request = ResearchRequest(
    token_address="So11111111111111111111111111111111111111112",
    chain="solana",
    symbol_hint="SOL",
    include_social=False,
    include_onchain=True,
    depth=ResearchDepth.STANDARD,
)

result = run_research(request)
print(result.success, result.error_message)
if result.success and result.report:
    print(result.report.full_report_markdown[:2000])
```

---

## 2) Deployment to Swarms Marketplace

TokenIntel is designed to be listed on the Swarms Marketplace.

### 2.1 Package core agents + tools into a single Swarms agent script

Run:

```powershell
cd /d D:\TokenIntel
python .\launch_helper.py
```

It generates:
- `dist\swarms-agent.py` (single-file agent script for Swarms)
- `dist\tokenintel_swarms_upload.zip` (upload bundle with `swarms-agent.py` + required companions)

### 2.2 Upload checklist

In the Swarms Marketplace UI:

1. Create a new agent listing
2. Upload `dist\tokenintel_swarms_upload.zip`
3. Ensure the entry function is `swarms_agent_run` (the generated single script exports it)
4. Add required environment/secrets in the marketplace settings:
   - `OPENAI_API_KEY` / `GROQ_API_KEY` / `ANTHROPIC_API_KEY` (at least one)
   - `BIRDEYE_API_KEY` (recommended)
   - `HELIUS_API_KEY` (optional but recommended for Solana analysis)
   - `X_BEARER_TOKEN` (optional; X may require credits)
5. Pin dependencies using the included `requirements.txt`

---

## 3) Exact Frenzy Mode launch steps

Frenzy mode is enabled here:

`https://swarms.world/launch?frenzy=true`

Step-by-step:

1. Open `https://swarms.world/launch?frenzy=true`
2. Connect the creator wallet (the wallet that will create the token)
3. Select the tokenized agent payload you uploaded from `dist\tokenintel_swarms_upload.zip`
4. Ensure the launch is set to Frenzy (2x fee multiplier)
5. Launch / tokenize

### 3.1 Tokenization command (Frenzy)

Use the Swarms Token Launch API with `fee_selection: "frenzy"`:

```powershell
curl.exe -Method POST "https://swarms.world/api/token/launch" `
  -H "Authorization: Bearer $env:SWARMS_API_KEY" `
  -H "Content-Type: application/json" `
  -d ( @{
    name="TokenIntel Research Agent";
    description="TokenIntel ($TINTEL) — multi-agent Web3 research report generator (on-chain + market + social/news).";
    ticker="TINTEL";
    private_key="$env:SWARMS_CREATOR_PRIVATE_KEY";
    fee_selection="frenzy";
    quote_mint="SOL";
  } | ConvertTo-Json -Compress )
```

Frenzy mode is higher visibility on the Frenzy leaderboard and uses a 2x fee multiplier. Token creation still requires SOL on the creator wallet.

---

## 4) How to set paid usage (0.01 SOL per report) + x402 micropayments

### 4.1 Marketplace paid usage

To charge `0.01 SOL` per report run:

1. Open your Swarms agent listing settings
2. Enable “Paid usage” / “Usage fee”
3. Set “Usage fee” to `0.01 SOL` per report generation
4. Enable payment enforcement middleware/integration for runtime calls (so the agent is executed only after payment)

### 4.2 x402 micropayments (HTTP 402 flow)

TokenIntel should enforce payment using the standard x402 pattern:

1. When the client hits the runtime endpoint without a payment signature, your server returns:
   - HTTP status: `402 Payment Required`
   - A `PAYMENT-REQUIRED` header containing payment instructions for x402
2. The client signs the payment (SOL or the facilitator-supported quote asset) and retries with the signature:
   - Add a `PAYMENT-SIGNATURE` header
3. The server verifies the signature, settles payment, then executes the agent and returns the report

Implementation shape on your side:
- Wrap the Swarms agent execution handler
- Gate the handler with x402 signature verification
- Only call Swarms + LLM tools after successful settlement

Notes:
- x402 is designed for “pay before compute”.
- If you want strict spend control, also rate limit and cache results for identical requests.

---

## 5) Revenue model explanation

With `0.01 SOL` paid usage per report:

- Users pay a fixed per-run research fee for compute-heavy multi-agent work.
- x402 ensures the payment is settled before LLM/data providers are called.
- Your revenue comes from:
  - the `0.01 SOL` usage fee (minus Swarms marketplace/protocol/platform fees)
  - pricing for compute-heavy calls is stabilized by caching and diskcache TTL
- Your variable costs include:
  - LLM token usage
  - Birdeye/Helius/API requests
  - chart generation and PDF/asset export (if enabled)

Operational recommendations:
- Raise `TINTEL_CACHE_TTL_SECONDS` in production to reduce repeated upstream calls.
- Keep `TINTEL_API_RATE_LIMIT_PER_MINUTE` aligned to avoid throttling.
- Disable optional sources (X) if a developer account lacks credits.

---

## 6) Security

- Never commit `.env` or real API keys to git or into marketplace bundles.
- Use marketplace secret storage / Swarms Secrets for runtime credentials.
- Treat reports as informational only; not financial advice.

---

## 7) GitHub & deployment

- **Repository:** [github.com/Somtiee/TokenIntel](https://github.com/Somtiee/TokenIntel)
- **Deploy guide:** see [`DEPLOY.md`](DEPLOY.md) for Streamlit Community Cloud, Render, and Vercel (custom domain / redirect).

**Important:** TokenIntel is a Streamlit app and cannot run as a Vercel serverless function. Host the full app on [Streamlit Cloud](https://share.streamlit.io) or [Render](https://render.com) (`render.yaml` included), then optionally use Vercel for a redirect or custom domain — details in `DEPLOY.md`.
