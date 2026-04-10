import requests
from datetime import datetime
import trafilatura
from concurrent.futures import ThreadPoolExecutor

import database
import utils
from db_connection import get_conn


ALPHA_VANTAGE_API_KEY=utils.get_env_variable('ALPHA_VANTAGE_API_KEY')
FINNHUB_API_KEY=utils.get_env_variable('FINNHUB_API_KEY')
POLYGON_API_KEY=utils.get_env_variable('POLYGON_API_KEY')
MAX_WORKERS = 10

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
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT DISTINCT ticker FROM ticker_artefacts
            WHERE artefact_type = 'header_description'
            ORDER BY ticker
        """)
        rows = c.fetchall()
        conn.close()
        return [r[0] for r in rows] if rows else ["NVDA"]
    except Exception as e:
        print(f"WARNING: could not query active tickers ({e}), falling back to NVDA")
        return ["NVDA"]


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


def fetch_single_url(article_data):
    """Helper for the thread pool. Returns (index, text)."""
    idx = article_data['index']
    url = article_data['url']
    
    if not url or url == "Unavailable":
        return idx, None
        
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded)
            return idx, text
    except:
        pass
    return idx, None

def parallel_fetch_texts(articles):
    """
    Fetches all article texts in parallel using threads.
    Returns a dict mapping {index: text}
    """
    print(f"  > Fetching {len(articles)} URLs in parallel...")
    
    tasks = [{'index': i, 'url': a.get('url')} for i, a in enumerate(articles)]
    results = {}
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_results = executor.map(fetch_single_url, tasks)
        for idx, text in future_results:
            results[idx] = text
            
    return results