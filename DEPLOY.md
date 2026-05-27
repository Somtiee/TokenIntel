# Deploy TokenIntel

TokenIntel is a **Streamlit** app with long-running multi-agent research (often 2–5 minutes). Plan hosting around a **persistent Python server**, not serverless-only platforms.

**Never commit `.env` or API keys.** Set secrets only in your host’s dashboard (Render, Streamlit Cloud, etc.).

---

## Quick comparison

| Platform | Full Streamlit app | Git push deploy | Secrets in UI | Notes |
|----------|-------------------|-----------------|---------------|--------|
| [Streamlit Community Cloud](https://share.streamlit.io) | Yes | Yes | Yes | Easiest for Streamlit |
| [Render](https://render.com) | Yes | Yes | Yes | Use included `render.yaml` |
| [Vercel](https://vercel.com) | **No** (serverless) | Yes | Yes | Use for **custom domain / redirect** only |

Vercel does **not** run Streamlit apps as serverless functions ([Streamlit community discussion](https://discuss.streamlit.io/t/deploy-streamlit-in-vercel/38070)). Use Vercel for a landing redirect or DNS, and host the app on Streamlit Cloud or Render.

---

## Option A — Streamlit Community Cloud (recommended)

1. Push this repo to GitHub: [Somtiee/TokenIntel](https://github.com/Somtiee/TokenIntel).
2. Go to [share.streamlit.io](https://share.streamlit.io) → **Create app**.
3. Connect **Somtiee/TokenIntel**, branch `main`.
4. Set **Main file path**: `main.py` (repo root).
5. **Advanced settings** → **Python version**: `3.11` (repo includes `.python-version`). Using 3.12+ can force `pandas` to compile from source and hang for 30+ minutes on `Preparing metadata (pyproject.toml)`.
6. **Advanced settings** → **Secrets** — paste (empty values; fill in the dashboard only):

```toml
OPENAI_API_KEY = ""
GROQ_API_KEY = ""
ANTHROPIC_API_KEY = ""
BIRDEYE_API_KEY = ""
HELIUS_API_KEY = ""
X_BEARER_TOKEN = ""
TINTEL_LLM_MODEL = "gpt-4o"
TINTEL_LOG_LEVEL = "INFO"
```

7. Deploy. Your app will be at `https://<app-name>.streamlit.app`.

If a build is stuck on `Preparing metadata (pyproject.toml)` for more than ~15 minutes: **Reboot app** after pulling latest `main` (uses prebuilt wheels for pandas/numpy).

---

## Option B — Render (Vercel-like git deploy)

1. Push to GitHub.
2. [Render Dashboard](https://dashboard.render.com) → **New** → **Blueprint** (or **Web Service**).
3. Connect **Somtiee/TokenIntel** — Render reads [`render.yaml`](render.yaml).
4. In the service → **Environment**, add the same keys as in [`.env.example`](.env.example) (never commit real values).
5. **Start command** (already in `render.yaml`):

```bash
streamlit run main.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true --browser.gatherUsageStats=false
```

6. After deploy, copy your live URL (e.g. `https://tokenintel.onrender.com`).

Free tier may sleep after inactivity; first load can take ~30s.

---

## Option C — Vercel (custom domain / redirect to the real app)

Use this when you want a **Vercel project** (e.g. `tokenintel.vercel.app` or your own domain) that forwards users to the hosted Streamlit/Render app.

### 1. Host the app first

Complete **Option A** or **Option B** and note the live URL.

### 2. Point Vercel at that URL

Edit [`vercel.json`](vercel.json) and [`public/index.html`](public/index.html): replace  
`https://REPLACE_WITH_YOUR_STREAMLIT_OR_RENDER_URL`  
with your real app URL (e.g. `https://tokenintel.onrender.com`).

### 3. Deploy on Vercel

1. [vercel.com](https://vercel.com) → **Add New Project** → import **Somtiee/TokenIntel**.
2. Framework preset: **Other** (static + redirects).
3. **Do not** add API keys to Vercel unless you build a separate API — the Streamlit app reads secrets from Render/Streamlit Cloud.
4. Deploy.

### 4. Optional — custom domain on Vercel

1. Vercel project → **Settings** → **Domains** → add `app.yourdomain.com`.
2. Update DNS at your registrar per Vercel’s instructions.
3. Users hitting your Vercel domain are redirected to the full TokenIntel UI on Render/Streamlit.

### Alternative — Vercel DNS only (no redirect project)

Point a CNAME from your domain to:

- `xxxx.streamlit.app` (Streamlit Cloud), or  
- your `*.onrender.com` hostname (Render),

and skip the Vercel redirect project.

---

## Environment variables (all hosts)

Copy from [`.env.example`](.env.example). Minimum:

| Variable | Required |
|----------|----------|
| `OPENAI_API_KEY` or `GROQ_API_KEY` or `ANTHROPIC_API_KEY` | At least one |
| `BIRDEYE_API_KEY` | Recommended (symbol search + market data) |
| `HELIUS_API_KEY` | Optional (Solana metadata) |
| `X_BEARER_TOKEN` | Optional (Reddit + RSS work without it) |

---

## Security checklist before `git push`

- [ ] `.env` is listed in `.gitignore` and **not** staged (`git status` must not show `.env`).
- [ ] Only `.env.example` is in the repo (placeholders, no real keys).
- [ ] No API keys in `main.py`, `config.py`, README, or commit messages.
- [ ] `agent_workspace/` and `.venv/` are not committed.

Verify:

```powershell
git status
git check-ignore -v .env
```

---

## Local run (reference)

```powershell
cd D:\TokenIntel
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# Edit .env locally — never commit
streamlit run main.py
```
