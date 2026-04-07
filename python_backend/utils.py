from datetime import datetime
import os
import json
import re
from dotenv import load_dotenv

load_dotenv()

def parse_datetime(date_string):
    """Attempts to parse various common date formats from APIs."""
    if not date_string:
        return None
    # Add more formats here if needed
    formats_to_try = [
        "%Y-%m-%dT%H:%M:%SZ",  # ISO 8601 (Polygon, MarketAux)
        "%Y-%m-%d %H:%M:%S",  # (Newsdata.io)
        "%Y%m%dT%H%M%S",  # (Alpha Vantage)
        "%Y-%m-%d",  # Date only
    ]
    for fmt in formats_to_try:
        try:
            return datetime.strptime(date_string, fmt)
        except (ValueError, TypeError):
            continue
    try:  # Fallback for Unix timestamps (Finnhub)
        return datetime.fromtimestamp(int(date_string))
    except (ValueError, TypeError):
        pass
    try:  # Fallback for ISO format from our cache file
        return datetime.fromisoformat(date_string)
    except (ValueError, TypeError):
        pass

    print(f"Warning: Could not parse date format for '{date_string}'")
    return None

def format_article(article):
    """Helper function to print article details in a readable format."""
    headline = article.get("headline", "N/A")
    provider = article.get("provider", "N/A")
    published_str = (
        article.get("published_at").strftime("%Y-%m-%d %H:%M:%S")
        if article.get("published_at")
        else "N/A"
    )
    url = article.get("url", "N/A")
    summary = article.get("summary", "N/A")

    print(f"--- {headline} ---")
    print(f"Provider: {provider} | Published: {published_str}")
    print(f"URL: {url}")
    print(f"Summary: {summary}\n")

def get_env_variable(name):
    """Retrieve an environment variable and show a user-friendly error if it's not found."""
    try:
        return os.environ[name]
    except KeyError:
        error_message = f"Required environment variable '{name}' not set. Please ensure it is defined in .env"
        raise EnvironmentError(error_message)
    
def clean_json_response(response_text):
    """
    Robust cleaning that extracts JSON structure from mixed text.
    """
    if not response_text: return []
    
    # 1. Try to find content between the first [ and the last ]
    try:
        match = re.search(r'\[.*\]', response_text, re.DOTALL)
        if match:
            json_str = match.group(0)
        else:
            # Fallback: maybe it's just a single object {} not in a list
            match = re.search(r'\{.*\}', response_text, re.DOTALL)
            json_str = f"[{match.group(0)}]" if match else response_text

        # 2. Cleanup common markdown
        json_str = re.sub(r"```json", "", json_str)
        json_str = re.sub(r"```", "", json_str)
        
        return json.loads(json_str)
        
    except json.JSONDecodeError as e:
        print(f"    ! JSON Parse Error: {e}")
        # Print a bit more of the raw text to help debug
        print(f"    ! Raw snippet: {response_text[:100]}...")
        return []
    

def csv_to_tickers(res):
    if not isinstance(res, str):
        print("The ticker finding llm failed to generate a string")
        return -1
    
    if 'boring!' in res:
        return []
    
    res.strip()

    if len(res) < 1:
        print("The ticker finding llm failed to generate a non-empty string")
        return -1

    if res[-1] == ',':
        res = res[:-1]

    if len(res) > 0:
        tickers = res.split(',')
        return tickers
    else:
        print("The ticker finding llm failed to generate a valid string")
        return -1
    
    