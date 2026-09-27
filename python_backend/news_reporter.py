from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple

import requests
import trafilatura
from trafilatura.settings import use_config

import database
import utils

ALPHA_VANTAGE_API_KEY=utils.get_env_variable('ALPHA_VANTAGE_API_KEY')
FINNHUB_API_KEY=utils.get_env_variable('FINNHUB_API_KEY')
POLYGON_API_KEY=utils.get_env_variable('POLYGON_API_KEY')
MAX_WORKERS = 10

# Seconds per fetch attempt. trafilatura retries internally, so worst-case
# wall time per URL is roughly 4x this — measured 40s at DOWNLOAD_TIMEOUT=10.
# At 10s an A/B/A test over 153 real article URLs extracted the same 121
# articles as the 30s default while cutting scrape wall time ~45%.
DOWNLOAD_TIMEOUT = 10

_TRAFILATURA_CFG = use_config()
_TRAFILATURA_CFG.set("DEFAULT", "DOWNLOAD_TIMEOUT", str(DOWNLOAD_TIMEOUT))

API_KEYS = {
    "finnhub": FINNHUB_API_KEY,
    "polygon": POLYGON_API_KEY,
    "alpha_vantage": ALPHA_VANTAGE_API_KEY,
}

PROVIDERS_TO_USE = [
    #'finnhub',
    #'polygon',
    'alpha_vantage'
]

TICKERS_PER_CYCLE = 2


def get_active_tickers() -> list[str]:
    return database.get_active_tickers()


def get_next_tickers(all_tickers: list[str]) -> list[str]:
    """Pick the next TICKERS_PER_CYCLE tickers using a rotating index stored in the DB."""
    idx = int(database.get_setting("fetch_rotation_index", "0"))
    n = len(all_tickers)
    selected = [all_tickers[(idx + i) % n] for i in range(TICKERS_PER_CYCLE)]
    database.set_setting("fetch_rotation_index", str((idx + TICKERS_PER_CYCLE) % n))
    print(f"  [rotation] index={idx}, selected={selected}")
    return selected

def get_alpha_vantage_news(api_key, ticker=None):
    """Fetches from Alpha Vantage and saves directly to DB.
    If the ticker is not given, pull all news. If the ticker is given, search for news related to that ticker.
    """


    print(f"Fetching Alpha Vantage news for ticker={ticker}...")
    
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "NEWS_SENTIMENT",
        "apikey": api_key,
        "limit": 1000
    }

    if ticker:
        params['tickers'] = ticker
    
    new_count = 0
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        feed = data.get("feed", [])
        print(f"  -> Found {len(feed)} items in feed.")

        for item in feed:
            # 1. Parse Data
            # Note: We map keys to match what database_v2.insert_raw_article expects
            article_payload = {
                "url": item.get("url"),
                "title": item.get("title"),          # DB expects 'title'
                "source": "Alpha Vantage",           # DB expects 'source'
                "summary": item.get("summary"),
                "time_published": utils.parse_datetime(item.get("time_published"))
            }
            
            # 2. Insert into DB
            # This function checks for duplicates automatically based on URL
            article_id = database.insert_raw_article(ticker, article_payload)
            
            if article_id:
                new_count += 1
                
        print(f"  -> Successfully saved {new_count} NEW articles to DB.")
        
    except Exception as e:
        print(f"  -> Error fetching Alpha Vantage: {e}")
    

def get_finnhub_news():
    pass

def get_polygon_news():
    pass


provider_functions = {
    "finnhub": get_finnhub_news,
    "polygon": get_polygon_news,
    "alpha_vantage": get_alpha_vantage_news,
}



def pull_all_news():
    all_tickers = get_active_tickers()
    tickers = get_next_tickers(all_tickers)
    print(f"[pull_all_news] Active tickers: {len(all_tickers)}, fetching this cycle: {tickers}")

    for provider in PROVIDERS_TO_USE:
        if provider in provider_functions:
            api_key = API_KEYS.get(provider)
            if not api_key or api_key == "YOUR_API_KEY_HERE":
                print(f"\nWarning: API key for {provider} is not set. Skipping.")
                continue

            for ticker in tickers:
                provider_functions[provider](api_key, ticker)
        else:
            print(f"\nWarning: Provider '{provider}' is not recognized.")

    print("\n--- Done. Provider news fetching loop completed")


class ScrapeResult(NamedTuple):
    """Outcome of one scrape. Exactly one of text / error is set."""
    text: str | None = None
    error: str | None = None


def fetch_single_url(url: str | None) -> ScrapeResult:
    """
    Download and extract one article. Never raises: a failed scrape is an
    expected outcome (~20% of URLs), so the reason is returned as data and
    ends up in articles.scrape_error.
    """
    if not url or url == "Unavailable":
        return ScrapeResult(error="no url")

    try:
        # fetch_response rather than fetch_url: fetch_url collapses every
        # download failure to None, fetch_response keeps the HTTP status.
        response = trafilatura.fetch_response(url, decode=True, config=_TRAFILATURA_CFG)
        if response is None:
            return ScrapeResult(error="no response (timeout or connection error)")
        if response.status != 200:
            return ScrapeResult(error=f"HTTP {response.status}")
        if not response.html:
            return ScrapeResult(error="empty response body")

        text = trafilatura.extract(response.html)
        if not text:
            # Page loaded but held no article: paywall, cookie wall, JS-rendered.
            return ScrapeResult(error="no extractable text")
        return ScrapeResult(text=text)

    except Exception as e:
        return ScrapeResult(error=f"{type(e).__name__}: {e}"[:300])


def parallel_fetch_texts(articles: list[dict]) -> list[ScrapeResult]:
    """
    Fetches all article texts in parallel using threads.
    Returns one ScrapeResult per article, in the same order as `articles`.
    """
    print(f"  > Fetching {len(articles)} URLs in parallel...")

    urls = [a.get("url") for a in articles]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # executor.map yields results in input order, so no index bookkeeping is needed.
        results = list(executor.map(fetch_single_url, urls))

    for url, result in zip(urls, results, strict=True):
        if result.error:
            print(f"  -> scrape failed ({result.error}): {url}")

    return results