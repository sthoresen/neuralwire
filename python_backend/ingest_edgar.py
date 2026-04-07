"""
ingest_edgar.py

Fetches earnings press releases from SEC EDGAR (8-K Exhibit 99.1) and stores
them in the earnings_documents table. Also backfills period_start / period_end
on earnings rows using EDGAR XBRL filing dates.

Usage:
    python ingest_edgar.py                      # dry run, NVDA
    python ingest_edgar.py --commit             # write to DB
    python ingest_edgar.py --ticker AAPL --commit
"""

import argparse
import os
import re
import sys

import requests
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import database

HEADERS = {'User-Agent': 'StockNewsApp research@stocknews.local'}
BASE    = 'https://data.sec.gov'
DEFAULT_TICKER = 'NVDA'


# ── EDGAR helpers ──────────────────────────────────────────────────────────────

def _get(url: str):
    r = requests.get(url, headers=HEADERS)
    r.raise_for_status()
    return r.json()


def _ticker_to_cik(ticker: str) -> str:
    tickers_map = _get('https://www.sec.gov/files/company_tickers.json')
    t = ticker.upper()
    for entry in tickers_map.values():
        if entry['ticker'] == t:
            return str(entry['cik_str']).zfill(10)
    raise ValueError(f'Ticker {t} not found in EDGAR')


def _get_earnings_8ks(cik: str) -> pd.DataFrame:
    """Returns a DataFrame of 8-K Item 2.02 filings (earnings announcements)."""
    sub = _get(f'{BASE}/submissions/CIK{cik}.json')
    recent = sub['filings']['recent']
    df = pd.DataFrame({
        'form':      recent['form'],
        'filed':     recent['filingDate'],
        'accession': recent['accessionNumber'],
        'items':     recent['items'],
    })
    eightk = df[df['form'] == '8-K']
    return eightk[eightk['items'].str.contains('2.02', na=False)].reset_index(drop=True)


def _get_xbrl_periods(cik: str) -> dict:
    """
    Returns dict: filed_date (str) -> (period_start, period_end).
    Built from EarningsPerShareDiluted XBRL records.
    - 10-Q: uses single-quarter records only (those with a 'frame' field set).
    - 10-K: uses the annual record.
    YTD multi-quarter 10-Q records (no frame) are skipped.
    """
    facts = _get(f'{BASE}/api/xbrl/companyfacts/CIK{cik}.json')
    gaap  = facts['facts'].get('us-gaap', {})
    if 'EarningsPerShareDiluted' not in gaap:
        return {}
    records = gaap['EarningsPerShareDiluted']['units']['USD/shares']
    result = {}
    for rec in records:
        form  = rec.get('form', '')
        filed = rec.get('filed', '')
        if not filed:
            continue
        if form == '10-Q' and not rec.get('frame'):
            continue  # skip YTD accumulations
        if filed not in result:
            result[filed] = (rec['start'], rec['end'])
    return result


def _get_exhibit_99_url(cik: str, accession: str) -> str | None:
    cik_int    = int(cik)
    acc_nodash = accession.replace('-', '')
    index_url  = (f'https://www.sec.gov/Archives/edgar/data/'
                  f'{cik_int}/{acc_nodash}/{accession}-index.htm')
    r = requests.get(index_url, headers=HEADERS)
    if r.status_code != 200:
        return None
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', r.text, re.DOTALL | re.IGNORECASE)
    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
        if len(cells) < 4:
            continue
        type_cell = re.sub(r'<[^>]+>', '', cells[3]).strip()
        if re.match(r'EX-99', type_cell, re.IGNORECASE):
            href_match = re.search(
                r'href="(/Archives/edgar/data/[^"]+)"', cells[2], re.IGNORECASE
            )
            if href_match:
                return 'https://www.sec.gov' + href_match.group(1)
    return None


def _fetch_text(url: str) -> str:
    import html as html_lib
    r = requests.get(url, headers=HEADERS)
    r.raise_for_status()
    html = r.text
    # Replace block-level tags (including all their attributes) with a newline
    html = re.sub(r'<(br|p|div|tr|li|h[1-6])[^>]*>', '\n', html, flags=re.IGNORECASE)
    # Strip all remaining tags
    text = re.sub(r'<[^>]+>', '', html)
    # Decode HTML entities (&#8226; → •, &amp; → &, etc.)
    text = html_lib.unescape(text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ── Core fetch + save ──────────────────────────────────────────────────────────

def _already_has_press_release(earnings_id: int) -> bool:
    docs = database.get_earnings_documents(earnings_id, doc_type='press_release')
    return len(docs) > 0


def fetch_and_save(ticker: str, earnings_row: dict,
                   eightk_by_date: dict, xbrl_periods: dict,
                   cik: str, commit: bool, force: bool = False) -> bool:
    """
    Processes one earnings row: finds the matching 8-K, fetches Exhibit 99.1,
    saves to earnings_documents, and backfills period_start / period_end.
    Returns True if a press release was saved (or would be in dry-run).
    """
    earnings_id  = earnings_row['id']
    report_date  = earnings_row['report_date']
    period_label = earnings_row.get('period_label', '')

    print(f"  {period_label} (report_date={report_date}) ...", end=" ", flush=True)

    if commit and _already_has_press_release(earnings_id):
        if not force:
            print("already in DB, skip")
            return False
        database.delete_earnings_documents(earnings_id, 'press_release')
        print("(replacing) ", end="")

    row_8k = eightk_by_date.get(report_date)
    if row_8k is None:
        print("no matching 8-K found")
        return False

    accession = row_8k['accession']
    period_start, period_end = xbrl_periods.get(report_date, (None, None))

    exhibit_url = _get_exhibit_99_url(cik, accession)
    if not exhibit_url:
        print(f"no Exhibit 99 in {accession}")
        return False

    try:
        text = _fetch_text(exhibit_url)
    except Exception as e:
        print(f"fetch error: {e}")
        return False

    word_count = len(text.split())
    print(f"{word_count} words", end=" ")

    if not commit:
        print("[dry run]")
        return True

    # Backfill period dates on the earnings row if not already set
    if period_start and period_end:
        database.get_or_create_earnings(
            ticker=ticker,
            report_date=report_date,
            period_start=period_start,
            period_end=period_end,
        )

    doc_id = database.save_earnings_document(
        earnings_id=earnings_id,
        doc_type='press_release',
        content=text,
        quality_score=85,
        is_preferred=True,
        source_url=exhibit_url,
    )

    if doc_id:
        period_info = f"{period_start} → {period_end}" if period_start else "no period dates"
        print(f"-> saved (doc_id={doc_id}, {period_info})")
        return True
    else:
        print("ERROR: document save failed")
        return False


# ── Main pipeline ──────────────────────────────────────────────────────────────

def run(ticker: str, commit: bool, force: bool = False):
    print(f"\nTicker : {ticker}")
    print(f"Mode   : {'COMMIT' if commit else 'DRY RUN'}")

    print("Resolving CIK ...", end=" ", flush=True)
    cik = _ticker_to_cik(ticker)
    print(cik)

    print("Fetching EDGAR 8-K filings ...", end=" ", flush=True)
    eightks = _get_earnings_8ks(cik)
    print(f"{len(eightks)} earnings 8-Ks found")
    eightk_by_date = {row['filed']: row for _, row in eightks.iterrows()}

    print("Fetching XBRL period dates ...", end=" ", flush=True)
    xbrl_periods = _get_xbrl_periods(cik)
    print(f"{len(xbrl_periods)} dated periods indexed")

    earnings_rows = database.get_earnings_list(ticker, limit=200)
    print(f"DB earnings rows : {len(earnings_rows)}\n")

    saved = 0
    for row in earnings_rows:
        ok = fetch_and_save(
            ticker=ticker,
            earnings_row=row,
            eightk_by_date=eightk_by_date,
            xbrl_periods=xbrl_periods,
            cik=cik,
            commit=commit,
            force=force,
        )
        if ok:
            saved += 1

    total = len(earnings_rows)
    print(f"\nDone. {saved}/{total} press releases {'saved' if commit else 'would be saved'}.")
    if not commit:
        print("Pass --commit to write to DB.")


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest earnings press releases from SEC EDGAR"
    )
    parser.add_argument("--ticker", default=DEFAULT_TICKER,
                        help="Ticker symbol (default: NVDA)")
    parser.add_argument("--commit", action="store_true",
                        help="Write to DB (default is dry run)")
    parser.add_argument("--force", action="store_true",
                        help="Re-fetch and overwrite existing press releases")
    args = parser.parse_args()
    run(ticker=args.ticker, commit=args.commit, force=args.force)
