"""Custom API tools: Birdeye, Helius, X/Twitter — with retries, rate limits, and cache."""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from functools import lru_cache, wraps
from typing import Any, ParamSpec, TypeVar

import requests
from diskcache import Cache
from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import Settings, get_settings

P = ParamSpec("P")
R = TypeVar("R")

_cache_lock = threading.Lock()
_disk_cache: Cache | None = None


def _get_disk_cache(settings: Settings) -> Cache:
    global _disk_cache
    if _disk_cache is None:
        with _cache_lock:
            if _disk_cache is None:
                _disk_cache = Cache(str(settings.tintel_cache_dir / "api"))
    return _disk_cache


class RateLimiter:
    """Simple sliding-window rate limiter (thread-safe)."""

    def __init__(self, max_calls: int, period_seconds: float = 60.0) -> None:
        self._max_calls = max_calls
        self._period = period_seconds
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            cutoff = now - self._period
            self._timestamps = [t for t in self._timestamps if t > cutoff]
            if len(self._timestamps) >= self._max_calls:
                sleep_for = self._period - (now - self._timestamps[0]) + 0.05
                if sleep_for > 0:
                    logger.debug("Rate limit reached; sleeping {:.2f}s", sleep_for)
                    time.sleep(sleep_for)
                self._timestamps = [t for t in self._timestamps if t > time.monotonic() - self._period]
            self._timestamps.append(time.monotonic())


def _settings_retry() -> Any:
    settings = get_settings()
    return retry(
        reraise=True,
        stop=stop_after_attempt(settings.tintel_max_retries),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((requests.RequestException, requests.Timeout)),
    )


def cached_api_call(namespace: str, ttl_seconds: int | None = None) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Disk cache decorator keyed by function name + arguments."""

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            settings = get_settings()
            cache = _get_disk_cache(settings)
            ttl = ttl_seconds if ttl_seconds is not None else settings.tintel_cache_ttl_seconds
            key = f"{namespace}:{func.__name__}:{json.dumps((args, kwargs), sort_keys=True, default=str)}"
            hit = cache.get(key)
            if hit is not None:
                logger.debug("Cache hit for {}", key[:80])
                return hit  # type: ignore[return-value]
            result = func(*args, **kwargs)
            cache.set(key, result, expire=ttl)
            return result

        return wrapper

    return decorator


_limiter: RateLimiter | None = None


def _rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(max_calls=get_settings().tintel_api_rate_limit_per_minute)
    return _limiter


@_settings_retry()
def _http_get(url: str, headers: dict[str, str] | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = get_settings()
    _rate_limiter().acquire()
    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=settings.tintel_request_timeout_seconds,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        return {"data": data}
    return data


_MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def is_valid_mint_address(value: str) -> bool:
    """True if value looks like a Solana-style base58 mint."""
    return bool(value and _MINT_RE.fullmatch(value.strip()))


@cached_api_call("birdeye_search", ttl_seconds=3600)
def birdeye_search_token(
    keyword: str,
    chain: str = "solana",
    limit: int = 5,
    search_mode: str = "exact",
) -> str:
    """
    Search Birdeye for a token by symbol or name.

    Returns:
        JSON string with matches: [{symbol, name, address, ...}, ...]
    """
    settings = get_settings()
    api_key = settings.secret_or_none(settings.birdeye_api_key)
    if not api_key:
        return json.dumps({"error": "BIRDEYE_API_KEY not configured", "keyword": keyword})

    url = "https://public-api.birdeye.so/defi/v3/search"
    headers = {"X-API-KEY": api_key, "x-chain": chain}
    params = {
        "keyword": keyword,
        "search_by": "symbol",
        "search_mode": search_mode,
        "limit": limit,
    }
    try:
        payload = _http_get(url, headers=headers, params=params)
        return json.dumps(payload, default=str)
    except requests.HTTPError as exc:
        logger.warning("Birdeye search failed: {}", exc)
        return json.dumps({"error": str(exc), "keyword": keyword})


def _birdeye_search_hits(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten Birdeye v3 search payload (items[].result[])."""
    hits: list[dict[str, Any]] = []
    raw_items = data.get("data", {}).get("items") if isinstance(data.get("data"), dict) else None
    if raw_items is None:
        raw_items = data.get("items") or []
    if not isinstance(raw_items, list):
        return hits
    for block in raw_items:
        if not isinstance(block, dict):
            continue
        nested = block.get("result")
        if isinstance(nested, list):
            for row in nested:
                if isinstance(row, dict):
                    hits.append(row)
        else:
            hits.append(block)
    return hits


def resolve_token_mint(query: str, chain: str = "solana") -> dict[str, str]:
    """
    Resolve user input (symbol or mint) to {mint, symbol, source}.

    Symbols must resolve via Birdeye (verified symbol → contract). Mint addresses pass through.
    """
    settings = get_settings()
    raw = (query or "").strip()
    if not raw:
        raise ValueError("Enter a token symbol or mint address.")

    token = raw.split("·")[0].split(",")[0].split()[0].strip()
    if token.startswith("$"):
        token = token[1:]

    if is_valid_mint_address(token):
        identity = lookup_token_identity(token, chain)
        return {
            "mint": token,
            "symbol": identity.get("symbol", ""),
            "name": identity.get("name", ""),
            "source": "mint",
        }

    symbol = token.upper()
    if len(symbol) < 2 or len(symbol) > 12:
        raise ValueError(f"Invalid symbol '{symbol}'. Use a ticker like JUP or a full mint address.")

    if not settings.secret_or_none(settings.birdeye_api_key):
        raise ValueError(
            f"Symbol '{symbol}' requires Birdeye lookup. Add BIRDEYE_API_KEY to .env, or paste the full mint address."
        )

    for mode in ("exact", "fuzzy"):
        raw_json = birdeye_search_token(symbol, chain=chain, limit=8, search_mode=mode)
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            continue
        if data.get("error"):
            continue

        items = _birdeye_search_hits(data)
        if not items:
            continue

        for item in items:
            addr = item.get("address") or item.get("mint") or item.get("token_address")
            sym = (item.get("symbol") or item.get("sym") or "").upper()
            if not addr:
                continue
            if mode == "exact" and sym and sym != symbol:
                continue
            name = str(item.get("name") or item.get("tokenName") or "").strip()
            return {
                "mint": str(addr),
                "symbol": sym or symbol,
                "name": name,
                "source": f"birdeye:{mode}",
            }

    raise ValueError(
        f"'{symbol}' is not a Birdeye-verified symbol on {chain}. "
        "Check spelling or paste the full contract (mint) address."
    )


# ---------------------------------------------------------------------------
# Birdeye
# ---------------------------------------------------------------------------


@cached_api_call("birdeye")
@lru_cache(maxsize=128)
def birdeye_token_overview(token_address: str, chain: str = "solana") -> str:
    """
    Fetch token overview from Birdeye (price, liquidity, market cap).

    Args:
        token_address: Token mint or contract address.
        chain: Chain slug supported by Birdeye (default solana).

    Returns:
        JSON string with overview metrics for agent consumption.
    """
    settings = get_settings()
    api_key = settings.secret_or_none(settings.birdeye_api_key)
    if not api_key:
        return json.dumps({"error": "BIRDEYE_API_KEY not configured", "token_address": token_address})

    url = "https://public-api.birdeye.so/defi/token_overview"
    headers = {"X-API-KEY": api_key, "x-chain": chain}
    params = {"address": token_address}
    try:
        payload = _http_get(url, headers=headers, params=params)
        return json.dumps(payload, default=str)
    except requests.HTTPError as exc:
        logger.warning("Birdeye overview failed: {}", exc)
        return json.dumps({"error": str(exc), "token_address": token_address})


@cached_api_call("birdeye")
def birdeye_price_history(token_address: str, chain: str = "solana", timeframe: str = "24h") -> str:
    """
    Fetch recent price history from Birdeye for charting and trend analysis.

    Args:
        token_address: Token mint or contract address.
        chain: Chain slug (default solana).
        timeframe: History window label (e.g. 24h, 7d).

    Returns:
        JSON string with OHLCV or price series data.
    """
    settings = get_settings()
    api_key = settings.secret_or_none(settings.birdeye_api_key)
    if not api_key:
        return json.dumps({"error": "BIRDEYE_API_KEY not configured"})

    url = "https://public-api.birdeye.so/defi/history_price"
    headers = {"X-API-KEY": api_key, "x-chain": chain}
    params = {"address": token_address, "address_type": "token", "type": timeframe}
    try:
        payload = _http_get(url, headers=headers, params=params)
        return json.dumps(payload, default=str)
    except requests.HTTPError as exc:
        logger.warning("Birdeye price history failed: {}", exc)
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Helius
# ---------------------------------------------------------------------------


@cached_api_call("helius")
def helius_token_metadata(token_address: str) -> str:
    """
    Fetch Solana token metadata and supply info via Helius DAS API.

    Args:
        token_address: Solana token mint address.

    Returns:
        JSON string with asset metadata, authorities, and supply fields.
    """
    settings = get_settings()
    api_key = settings.secret_or_none(settings.helius_api_key)
    if not api_key:
        return json.dumps({"error": "HELIUS_API_KEY not configured", "mint": token_address})

    url = f"https://mainnet.helius-rpc.com/?api-key={api_key}"
    body = {
        "jsonrpc": "2.0",
        "id": "tokenintel",
        "method": "getAsset",
        "params": {"id": token_address},
    }
    _rate_limiter().acquire()
    try:
        response = requests.post(url, json=body, timeout=settings.tintel_request_timeout_seconds)
        response.raise_for_status()
        return json.dumps(response.json(), default=str)
    except requests.RequestException as exc:
        logger.warning("Helius metadata failed: {}", exc)
        return json.dumps({"error": str(exc), "mint": token_address})


@cached_api_call("helius")
def helius_holder_insights(token_address: str) -> str:
    """
    Summarize holder distribution signals for a Solana token using Helius.

    Args:
        token_address: Solana token mint address.

    Returns:
        JSON string with holder-related fields when available.
    """
    meta_raw = helius_token_metadata(token_address)
    meta = json.loads(meta_raw)
    result = {
        "mint": token_address,
        "source": "helius",
        "asset": meta.get("result"),
        "note": "Use asset.token_info and authorities for risk flags.",
    }
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Token identity
# ---------------------------------------------------------------------------


def lookup_token_identity(mint: str, chain: str = "solana") -> dict[str, str]:
    """Resolve symbol and human-readable name from Birdeye overview."""
    try:
        raw = birdeye_token_overview(mint, chain)
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"mint": mint, "symbol": "", "name": ""}
    if data.get("error"):
        return {"mint": mint, "symbol": "", "name": ""}
    inner = data.get("data", data)
    if not isinstance(inner, dict):
        return {"mint": mint, "symbol": "", "name": ""}
    symbol = str(inner.get("symbol") or inner.get("sym") or "").upper().strip().lstrip("$")
    name = str(inner.get("name") or inner.get("tokenName") or inner.get("token_name") or "").strip()
    return {"mint": mint, "symbol": symbol, "name": name}


# ---------------------------------------------------------------------------
# Sentiment lexicon (free — no API keys)
# ---------------------------------------------------------------------------

_BULLISH_WORDS = frozenset(
    {
        "bull",
        "bullish",
        "moon",
        "pump",
        "breakout",
        "accumulate",
        "buy",
        "long",
        "ath",
        "rip",
        "green",
        "rally",
        "surge",
        "gains",
    }
)
_BEARISH_WORDS = frozenset(
    {
        "bear",
        "bearish",
        "dump",
        "rug",
        "scam",
        "sell",
        "short",
        "crash",
        "rekt",
        "red",
        "fade",
        "down",
        "bleed",
    }
)


def score_texts_sentiment(texts: list[str]) -> float:
    """Lexical sentiment in [-1, 1] from post titles/bodies."""
    if not texts:
        return 0.0
    total = 0
    for text in texts:
        words = re.findall(r"[a-z]{3,}", (text or "").lower())
        for word in words:
            if word in _BULLISH_WORDS:
                total += 1
            elif word in _BEARISH_WORDS:
                total -= 1
    if total == 0:
        return 0.0
    normalized = total / max(len(texts), 1)
    return max(-1.0, min(1.0, normalized / 4.0))


# ---------------------------------------------------------------------------
# Free social (Reddit) + news (RSS) — no X bearer required
# ---------------------------------------------------------------------------


@cached_api_call("reddit", ttl_seconds=300)
def reddit_token_posts(symbol: str, limit: int = 15) -> str:
    """Fetch recent Reddit posts mentioning a token symbol (no API key)."""
    settings = get_settings()
    sym = (symbol or "").upper().replace("$", "")
    if not sym:
        return json.dumps({"error": "symbol required", "posts": []})

    url = "https://www.reddit.com/search.json"
    params = {"q": f"${sym} OR {sym} crypto solana", "limit": min(limit, 25), "sort": "new"}
    headers = {"User-Agent": "TokenIntel/1.0 (research; +https://github.com/tokenintel)"}
    try:
        _rate_limiter().acquire()
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=settings.tintel_request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        logger.warning("Reddit search failed: {}", exc)
        return json.dumps({"error": str(exc), "source": "reddit", "posts": []})

    posts: list[dict[str, Any]] = []
    children = payload.get("data", {}).get("children", [])
    for child in children:
        if not isinstance(child, dict):
            continue
        post = child.get("data", {})
        if not isinstance(post, dict):
            continue
        title = str(post.get("title", ""))
        selftext = str(post.get("selftext", ""))[:400]
        text = f"{title}. {selftext}".strip()
        posts.append(
            {
                "text": text,
                "source": "reddit",
                "subreddit": post.get("subreddit"),
                "score": post.get("score"),
                "url": f"https://reddit.com{post.get('permalink', '')}",
                "created_utc": post.get("created_utc"),
            }
        )

    texts = [p["text"] for p in posts if p.get("text")]
    return json.dumps(
        {
            "source": "reddit",
            "query": sym,
            "count": len(posts),
            "posts": posts,
            "sentiment_score": score_texts_sentiment(texts),
        },
        default=str,
    )


@cached_api_call("rss_news", ttl_seconds=600)
def fetch_token_news_free(symbol: str, limit: int = 8) -> str:
    """Crypto headlines from public RSS feeds (no API key)."""
    sym = (symbol or "").upper().replace("$", "")
    try:
        import feedparser
    except ImportError:
        return json.dumps({"error": "feedparser not installed", "news": []})

    feeds = (
        ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
        ("CoinTelegraph", "https://cointelegraph.com/rss"),
    )
    items: list[dict[str, Any]] = []
    sym_lower = sym.lower() if sym else ""

    for source, feed_url in feeds:
        try:
            parsed = feedparser.parse(feed_url)
        except Exception as exc:
            logger.debug("RSS parse failed {}: {}", source, exc)
            continue
        for entry in parsed.entries[:30]:
            title = str(getattr(entry, "title", "") or "")
            if sym_lower and sym_lower not in title.lower() and sym_lower not in str(getattr(entry, "summary", "")).lower():
                continue
            link = str(getattr(entry, "link", "") or "")
            published = str(getattr(entry, "published", "") or getattr(entry, "updated", "") or "")
            items.append({"title": title, "url": link, "source": source, "timestamp": published})
            if len(items) >= limit:
                break
        if len(items) >= limit:
            break

    if not items and sym:
        for source, feed_url in feeds:
            try:
                parsed = feedparser.parse(feed_url)
            except Exception:
                continue
            for entry in parsed.entries[:limit]:
                items.append(
                    {
                        "title": str(getattr(entry, "title", "") or ""),
                        "url": str(getattr(entry, "link", "") or ""),
                        "source": source,
                        "timestamp": str(getattr(entry, "published", "") or ""),
                    }
                )
            if items:
                break

    return json.dumps({"symbol": sym, "count": len(items), "news": items[:limit]}, default=str)


# ---------------------------------------------------------------------------
# X / Twitter (optional — paid API)
# ---------------------------------------------------------------------------


def _build_x_search_query(symbol: str | None = None, query: str = "") -> str:
    """X API v2 recent-search query (cashtag + keyword, English, no retweets)."""
    sym = (symbol or query or "").upper().replace("$", "").strip()
    if sym and len(sym) <= 12 and sym.isalnum():
        return f"(${sym} OR {sym} crypto) -is:retweet lang:en"
    q = (query or "").strip()
    if not q:
        return "crypto -is:retweet lang:en"
    return f"{q} -is:retweet lang:en"


def verify_x_connection() -> dict[str, Any]:
    """Quick Bearer-token check (no secrets returned)."""
    settings = get_settings()
    bearer = settings.secret_or_none(settings.x_bearer_token)
    if not bearer:
        return {"ok": False, "message": "X_BEARER_TOKEN not set in .env"}

    try:
        import tweepy

        client = tweepy.Client(bearer_token=bearer, wait_on_rate_limit=True)
        _rate_limiter().acquire()
        response = client.search_recent_tweets(
            query="crypto -is:retweet lang:en",
            max_results=10,
            tweet_fields=["created_at"],
        )
        count = len(response.data or [])
        return {"ok": True, "message": f"Bearer token valid — search API returned {count} tweets"}
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "402" in msg or "Payment Required" in msg or "credits" in msg.lower():
            return {"ok": True, "no_credits": True}
        return {"ok": False, "message": msg}


@cached_api_call("twitter", ttl_seconds=120)
def x_token_sentiment(query: str, max_results: int = 20, symbol: str | None = None) -> str:
    """
    Search recent posts on X (Twitter) for token-related sentiment.

    Args:
        query: Search query (symbol, cashtag, or contract snippet).
        max_results: Maximum tweets to return (capped at 50).
        symbol: Optional ticker for optimized cashtag search.

    Returns:
        JSON string with tweets and basic sentiment hints for agents.
    """
    settings = get_settings()
    bearer = settings.secret_or_none(settings.x_bearer_token)
    if not bearer:
        return json.dumps({"error": "X_BEARER_TOKEN not configured", "query": query})

    search_q = _build_x_search_query(symbol=symbol, query=query)
    max_results = min(max(10, max_results), 50)
    try:
        import tweepy

        client = tweepy.Client(bearer_token=bearer, wait_on_rate_limit=True)
        _rate_limiter().acquire()
        response = client.search_recent_tweets(
            query=search_q,
            max_results=max_results,
            tweet_fields=["created_at", "public_metrics", "lang"],
        )
        tweets: list[dict[str, Any]] = []
        if response.data:
            for tweet in response.data:
                tweets.append(
                    {
                        "id": str(tweet.id),
                        "text": tweet.text,
                        "metrics": getattr(tweet, "public_metrics", None),
                    }
                )
        texts = [t["text"] for t in tweets if t.get("text")]
        return json.dumps(
            {
                "query": search_q,
                "count": len(tweets),
                "tweets": tweets,
                "source": "x",
                "sentiment_score": score_texts_sentiment(texts),
            },
            default=str,
        )
    except Exception as exc:  # noqa: BLE001 — surface to LLM as structured JSON
        logger.warning("X search failed: {}", exc)
        err = str(exc)
        if "402" in err or "Payment Required" in err:
            err = "X API unavailable"
        return json.dumps({"error": err, "query": search_q, "source": "x"})


@cached_api_call("social", ttl_seconds=180)
def token_social_sentiment(query: str, symbol: str | None = None, max_results: int = 20) -> str:
    """Social sentiment: optional X API + free Reddit (no bearer required for Reddit)."""
    sym = (symbol or query or "").upper().replace("$", "").strip()
    posts: list[dict[str, Any]] = []
    sources: list[str] = []

    x_raw = x_token_sentiment(query, max_results=max_results, symbol=sym or None)
    try:
        x_data = json.loads(x_raw)
    except json.JSONDecodeError:
        x_data = {}
    if not x_data.get("error"):
        for tw in x_data.get("tweets", []):
            if isinstance(tw, dict) and tw.get("text"):
                posts.append(
                    {
                        "text": tw["text"],
                        "source": "x",
                        "url": f"https://twitter.com/i/web/status/{tw.get('id', '')}",
                    }
                )
        if posts:
            sources.append("x")

    if sym:
        reddit_raw = reddit_token_posts(sym, limit=max_results)
        try:
            reddit_data = json.loads(reddit_raw)
        except json.JSONDecodeError:
            reddit_data = {}
        for post in reddit_data.get("posts", []):
            if isinstance(post, dict) and post.get("text"):
                posts.append(post)
        if reddit_data.get("posts"):
            sources.append("reddit")

    texts = [str(p.get("text", "")) for p in posts if p.get("text")]
    score = score_texts_sentiment(texts)
    if score == 0.0 and x_data.get("sentiment_score") is not None:
        score = float(x_data["sentiment_score"])

    return json.dumps(
        {
            "query": query,
            "symbol": sym,
            "count": len(posts),
            "posts": posts[:max_results],
            "tweets": posts[:max_results],
            "sources": sources or ["none"],
            "sentiment_score": score,
        },
        default=str,
    )


def all_agent_tools() -> list[Callable[..., str]]:
    """Tool functions registered with Swarms agents."""
    return [
        birdeye_token_overview,
        birdeye_price_history,
        helius_token_metadata,
        helius_holder_insights,
        token_social_sentiment,
        fetch_token_news_free,
        reddit_token_posts,
        x_token_sentiment,
    ]
