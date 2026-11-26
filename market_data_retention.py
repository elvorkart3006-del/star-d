"""Helpers that keep exact market prices out of persistent research exports."""

import re


_PRICE_SUFFIXES = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "price",
    "ltp",
    "last_price",
    "quote_price",
    "fill_price",
    "mark",
    "basis",
)
_QUOTE_TIMESTAMPS = {
    "quote_at",
    "quote_time",
    "quote_time_ist",
    "last_trade_time",
    "last_trade_time_ist",
}


def is_raw_market_column(column):
    """Return True for a column that contains an exact market price or quote time."""
    name = re.sub(r"[^a-z0-9]+", "_", str(column).lower()).strip("_")
    if name in _QUOTE_TIMESTAMPS:
        return True
    if name in _PRICE_SUFFIXES:
        return True
    return any(
        name == suffix + "_inr"
        or name == suffix + "_price"
        or name == suffix + "_price_inr"
        or name.endswith("_" + suffix)
        or name.endswith("_" + suffix + "_inr")
        or name.endswith("_" + suffix + "_price")
        or name.endswith("_" + suffix + "_price_inr")
        for suffix in _PRICE_SUFFIXES
    )


def strip_raw_market_columns(frame):
    """Return a copy without exact price, volume, or last-trade-time fields."""
    blocked = [column for column in frame.columns if is_raw_market_column(column)]
    return frame.drop(columns=blocked).copy()
