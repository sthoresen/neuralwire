"""
ticker_colors.py

Accent color resolution for stock tickers used in the frontend.

Priority chain (highest → lowest):
  1. 'manual'    — user-set via set_ticker_color(), never overwritten
  2. 'brand'     — from BRAND_COLORS dict, seeded on init()
  3. 'generated' — asked from an LLM, cached after first lookup

Public API:
  init()                      — create DB table + seed brand colors
  resolve(ticker) -> str      — always returns a hex color (generates if needed)
  generate(ticker) -> str     — explicit LLM call + store
  darken_hex(hex, amount=12)  — reduce lightness (light-mode accent variant)
  hex_to_rgba(hex, alpha)     — CSS rgba() string
"""

import colorsys
import re

import database

# Known brand / product colors for popular tickers.
BRAND_COLORS: dict[str, str] = {
    "NVDA": "#76b900",  # NVIDIA green
    "AAPL": "#555555",  # Apple grey
    "MSFT": "#00a4ef",  # Microsoft blue
    "AMZN": "#ff9900",  # Amazon orange
    "TSLA": "#e31937",  # Tesla red
    "GOOG": "#4285f4",  # Google blue
    "GOOGL": "#4285f4",
    "META": "#0866ff",  # Meta blue
    "AMD": "#ed1c24",  # AMD red
    "INTC": "#0071c5",  # Intel blue
    "QCOM": "#3253dc",  # Qualcomm blue
    "AVGO": "#cc092f",  # Broadcom red
    "ORCL": "#f80000",  # Oracle red
    "CRM": "#00a1e0",  # Salesforce blue
    "NFLX": "#e50914",  # Netflix red
    "UBER": "#276ef1",  # Uber blue
    "ABNB": "#ff5a5f",  # Airbnb coral
    "COIN": "#0052ff",  # Coinbase blue
    "PLTR": "#7b2d8b",  # Palantir purple
    "SNOW": "#29b5e8",  # Snowflake blue
    "SHOP": "#96bf48",  # Shopify green
    "SPOT": "#1db954",  # Spotify green
    "SQ": "#00b140",  # Block/Square green
    "PYPL": "#003087",  # PayPal blue
    "V": "#1a1f71",  # Visa blue
    "MA": "#eb001b",  # Mastercard red
    "JPM": "#005696",  # JPMorgan blue
    "GS": "#6d9fcc",  # Goldman Sachs blue
    "BAC": "#e31837",  # BofA red
    "WMT": "#0071ce",  # Walmart blue
    "DIS": "#113ccf",  # Disney blue
    "F": "#003da5",  # Ford blue
    "TSM": "#ff6a00",  # TSMC red-orange
}

_FALLBACK_COLOR = "#6366f1"  # indigo — neutral if LLM fails


# ── Initialisation ────────────────────────────────────────────────────────────


def init() -> None:
    """Create the DB table and seed all BRAND_COLORS (respects manual overrides)."""
    database.init_ticker_colors_table()
    for ticker, color in BRAND_COLORS.items():
        database.set_ticker_color(ticker, color, source="brand")


# ── Resolution ────────────────────────────────────────────────────────────────


def resolve(ticker: str) -> str:
    """
    Return an accent color for the ticker — always succeeds.

    Checks the DB first (covers manual, brand, and previously generated entries).
    If nothing is stored, calls generate() which asks an LLM and caches the result.
    """
    color = database.get_ticker_color(ticker)
    if color:
        return color
    return generate(ticker)


def generate(ticker: str) -> str:
    """
    Ask a cheap LLM for a fitting hex accent color for the ticker.
    Stores the result as source='generated' and returns the hex string.
    Falls back to _FALLBACK_COLOR if the LLM call fails or returns garbage.
    """
    from llms import LLMProviderManager  # lazy import — llms.py has heavy setup

    llm_manager = LLMProviderManager()

    cls = database.get_ticker_classification(ticker)
    long_name = cls.get("long_name") if cls else None
    company = f"{long_name} ({ticker})" if long_name else f"ticker symbol {ticker}"

    prompt = (
        f"What single hex color code (#rrggbb) best represents the primary brand color of {company}? "
        f"Use the company's most iconic logo or brand color — the one a designer would pick. "
        f"Reply with ONLY the hex code — no explanation, no extra text."
    )
    raw, _ = llm_manager.call(prompt, tier="standard", reasoning=False, max_tokens=20)
    if not raw:
        raise RuntimeError(
            f"[ticker_colors] All cheap providers failed for {ticker} — no color generated."
        )

    match = re.search(r"#[0-9a-fA-F]{6}", raw)
    if not match:
        raise RuntimeError(f"[ticker_colors] LLM returned no valid hex color for {ticker}: {raw!r}")

    color = match.group(0).lower()
    database.set_ticker_color(ticker, color, source="generated")
    return color


# ── Color utilities ───────────────────────────────────────────────────────────


def darken_hex(hex_color: str, amount: int = 12) -> str:
    """
    Return a darker variant by reducing HSL lightness by `amount` percentage points.
    Used to produce a light-mode accent from the base (dark-mode optimised) color.
    """
    h = hex_color.lstrip("#")
    r, g, b = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    hue, lightness, s = colorsys.rgb_to_hls(r, g, b)  # colorsys uses H,L,S order
    lightness = max(0.0, lightness - amount / 100)
    r2, g2, b2 = colorsys.hls_to_rgb(hue, lightness, s)
    return f"#{int(r2 * 255 + 0.5):02x}{int(g2 * 255 + 0.5):02x}{int(b2 * 255 + 0.5):02x}"


def hex_to_rgba(hex_color: str, alpha: float) -> str:
    """Convert a hex color + alpha value to a CSS rgba() string."""
    h = hex_color.lstrip("#")
    r, g, b = [int(h[i : i + 2], 16) for i in (0, 2, 4)]
    return f"rgba({r},{g},{b},{alpha})"
