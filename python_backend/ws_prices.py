"""
ws_prices.py — Phase 4A/4B (real-time WebSocket scaffold)

Opens an Alpaca WebSocket connection to the IEX feed and streams
live trade ticks for a target ticker to the console.

This is a verification/dev script — the goal is to confirm the stream
is working before wiring it into any frontend.

Usage:
    python ws_prices.py              # streams NVDA by default
    python ws_prices.py --ticker AAPL
    python ws_prices.py --ticker NVDA --bars  # subscribe to minute bars instead of trades

Press Ctrl+C to stop.

Feed: wss://stream.data.alpaca.markets/v2/iex (free tier)
"""

import argparse
from datetime import datetime

from alpaca_trade_api.stream import Stream

import utils

ALPACA_KEY = utils.get_env_variable("ALPACA_KEY")
ALPACA_SECRET = utils.get_env_variable("ALPACA_SECRET")


def make_trade_handler(ticker: str):
    async def handler(t):
        ts = datetime.fromtimestamp(t.timestamp / 1e9).strftime("%H:%M:%S.%f")[:-3]
        print(
            f"[TRADE] {ticker}  {ts}  price={t.price:<10.2f}  size={t.size:<8}  exchange={t.exchange}"
        )

    return handler


def make_bar_handler(ticker: str):
    async def handler(b):
        print(
            f"[BAR]   {ticker}  {b.timestamp}  "
            f"O={b.open:.2f}  H={b.high:.2f}  L={b.low:.2f}  C={b.close:.2f}  "
            f"V={b.volume}"
        )

    return handler


def make_quote_handler(ticker: str):
    async def handler(q):
        ts = datetime.fromtimestamp(q.timestamp / 1e9).strftime("%H:%M:%S.%f")[:-3]
        print(
            f"[QUOTE] {ticker}  {ts}  bid={q.bid_price:<8.2f}x{q.bid_size:<6}  ask={q.ask_price:<8.2f}x{q.ask_size}"
        )

    return handler


def main():
    parser = argparse.ArgumentParser(description="Alpaca real-time WebSocket stream")
    parser.add_argument("--ticker", default="NVDA")
    parser.add_argument(
        "--bars", action="store_true", help="Subscribe to minute bars instead of trades"
    )
    parser.add_argument("--quotes", action="store_true", help="Also subscribe to quotes (bid/ask)")
    args = parser.parse_args()

    ticker = args.ticker.upper()

    stream = Stream(
        key_id=ALPACA_KEY,
        secret_key=ALPACA_SECRET,
        base_url="https://stream.data.alpaca.markets",
        data_feed="iex",
        raw_data=False,
    )

    if args.bars:
        stream.subscribe_bars(make_bar_handler(ticker), ticker)
        print(f"Subscribed to MINUTE BARS for {ticker} (IEX feed)")
    else:
        stream.subscribe_trades(make_trade_handler(ticker), ticker)
        print(f"Subscribed to TRADES for {ticker} (IEX feed)")

    if args.quotes:
        stream.subscribe_quotes(make_quote_handler(ticker), ticker)
        print(f"Subscribed to QUOTES for {ticker}")

    print("Connecting... (Ctrl+C to stop)\n")
    stream.run()


if __name__ == "__main__":
    main()
