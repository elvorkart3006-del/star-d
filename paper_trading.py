"""Local, long-only paper account. This module receives prices as data."""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

from backtest_with_costs import SLIPPAGE_RATE, calculate_charges


IST = ZoneInfo("Asia/Kolkata")
JOURNAL_PATH = Path(__file__).resolve().parent / "data" / "paper_trading.sqlite3"
MAX_CAPITAL = 1_000_000_000
MAX_QUANTITY = 1_000_000
MAX_QUOTE_AGE = timedelta(minutes=5)

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS account (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        capital INTEGER NOT NULL, cash INTEGER NOT NULL,
        position_pct INTEGER NOT NULL, max_positions INTEGER NOT NULL,
        stop_pct INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS positions (
        symbol TEXT PRIMARY KEY, quantity INTEGER NOT NULL CHECK (quantity > 0),
        cost INTEGER NOT NULL, stop INTEGER NOT NULL,
        last_price INTEGER NOT NULL, quote_time TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY,
        executed_at TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
        quantity INTEGER NOT NULL, quote_price INTEGER NOT NULL, quote_time TEXT NOT NULL,
        fill_price INTEGER NOT NULL, fees INTEGER NOT NULL, slippage INTEGER NOT NULL,
        realized_pnl INTEGER, cash_after INTEGER NOT NULL, reason TEXT NOT NULL
    )""",
)


class PaperTradeError(ValueError):
    """A simulated trade or account setting was rejected."""


@dataclass(frozen=True)
class Settings:
    capital: int = 10_000_000  # Paise, as are all stored money amounts.
    position_pct: int = 25
    max_positions: int = 5
    stop_pct: int = 5


@dataclass(frozen=True)
class Quote:
    price: int
    at: datetime

    def is_fresh(self, now):
        age = now - self.at
        return (
            in_session(now)
            and in_session(self.at)
            and self.at.date() == now.date()
            and timedelta(0) <= age <= MAX_QUOTE_AGE
        )


@dataclass(frozen=True)
class Portfolio:
    settings: Settings
    cash: int
    positions: dict[str, sqlite3.Row]
    journal: list[sqlite3.Row]

    @property
    def market_value(self):
        return sum(row["quantity"] * row["last_price"] for row in self.positions.values())

    @property
    def unrealized(self):
        return self.market_value - sum(row["cost"] for row in self.positions.values())

    @property
    def realized(self):
        return sum(row["realized_pnl"] or 0 for row in self.journal)

    @property
    def costs(self):
        return sum(row["fees"] + row["slippage"] for row in self.journal)


def now_ist():
    return datetime.now(IST)


def in_session(moment):
    return time(9, 15) <= moment.astimezone(IST).time() < time(15, 30)


def to_paise(amount):
    """Round one rupee amount to integer paise."""
    try:
        value = Decimal(str(amount))
        if not value.is_finite() or abs(value) > MAX_CAPITAL:
            raise InvalidOperation
        return int((value * 100).to_integral_value(rounding=ROUND_HALF_UP))
    except InvalidOperation as error:
        raise PaperTradeError("Enter a valid amount in INR.") from error


class PaperAccount:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else JOURNAL_PATH

    @contextmanager
    def _database(self, write=False):
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                if write:
                    for statement in SCHEMA:
                        db.execute(statement)
                    defaults = Settings()
                    db.execute(
                        "INSERT OR IGNORE INTO account VALUES (1, ?, ?, ?, ?, ?)",
                        (defaults.capital, defaults.capital, defaults.position_pct,
                         defaults.max_positions, defaults.stop_pct),
                    )
                yield db
        finally:
            db.close()

    def snapshot(self):
        defaults = Settings()
        if self.path.is_file():
            with self._database() as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'account'").fetchone():
                    row = db.execute("SELECT * FROM account WHERE id = 1").fetchone()
                    return Portfolio(
                        Settings(row["capital"], row["position_pct"], row["max_positions"], row["stop_pct"]),
                        row["cash"],
                        {position["symbol"]: position for position in db.execute(
                            "SELECT * FROM positions ORDER BY symbol"
                        )},
                        db.execute("SELECT * FROM trades ORDER BY id DESC").fetchall(),
                    )
        return Portfolio(defaults, defaults.capital, {}, [])

    def configure(self, settings):
        for value, upper, label in (
            (settings.capital, MAX_CAPITAL * 100, "Virtual capital"),
            (settings.position_pct, 100, "Position limit"),
            (settings.max_positions, 50, "Open position limit"),
            (settings.stop_pct, 50, "Stop-loss percentage"),
        ):
            if type(value) is not int or not 1 <= value <= upper:
                raise PaperTradeError(f"{label} is outside the allowed range.")

        with self._database(write=True) as db:
            row = db.execute("SELECT capital, cash FROM account WHERE id = 1").fetchone()
            delta = settings.capital - row["capital"]
            if delta < 0 and row["cash"] + delta < 0:
                raise PaperTradeError("Reduce virtual capital only by the available paper cash.")
            db.execute(
                """UPDATE account SET capital = ?, cash = cash + ?, position_pct = ?,
                   max_positions = ?, stop_pct = ? WHERE id = 1""",
                (settings.capital, delta, settings.position_pct,
                 settings.max_positions, settings.stop_pct),
            )

    def apply_quotes(self, quotes, now):
        """Mark holdings and atomically execute any stops hit by fresh quotes."""
        if not quotes or not self.path.is_file():
            return []
        stopped = []
        with self._database(write=True) as db:
            for position in db.execute("SELECT * FROM positions ORDER BY symbol").fetchall():
                symbol = position["symbol"]
                quote = quotes.get(symbol)
                if (
                    quote is None or quote.price <= 0 or quote.at > now
                    or quote.at.isoformat() < position["quote_time"]
                ):
                    continue
                if quote.price <= position["stop"] and quote.is_fresh(now):
                    self._fill(db, symbol, "SELL", position["quantity"], quote, now, position, "Stop-loss")
                    stopped.append(symbol)
                else:
                    db.execute(
                        "UPDATE positions SET last_price = ?, quote_time = ? WHERE symbol = ?",
                        (quote.price, quote.at.isoformat(), symbol),
                    )
        return stopped

    def trade(self, symbol, side, quantity, quote, now):
        if side not in ("BUY", "SELL") or not symbol:
            raise PaperTradeError("Choose an NSE stock and a paper trade side.")
        if type(quantity) is not int or not 1 <= quantity <= MAX_QUANTITY:
            raise PaperTradeError("Enter a positive whole number of shares within the quantity limit.")
        if quote is None or quote.price <= 0 or not quote.is_fresh(now):
            raise PaperTradeError("A fresh Zerodha last trade during the NSE session is required.")

        with self._database(write=True) as db:
            position = db.execute("SELECT * FROM positions WHERE symbol = ?", (symbol,)).fetchone()
            if position and quote.at.isoformat() < position["quote_time"]:
                raise PaperTradeError("A newer price is already available. Try the paper trade again.")
            if side == "SELL" and (position is None or position["quantity"] < quantity):
                raise PaperTradeError("You can sell only shares you hold in the paper account.")
            if side == "BUY" and position and quote.price <= position["stop"]:
                raise PaperTradeError("This position has reached its paper stop-loss. Refresh its price first.")
            return self._fill(db, symbol, side, quantity, quote, now, position, "Manual")

    def _fill(self, db, symbol, side, quantity, quote, now, position, reason):
        account = db.execute("SELECT * FROM account WHERE id = 1").fetchone()
        price_move = to_paise(Decimal(quote.price) / 100 * Decimal(str(SLIPPAGE_RATE)))
        fill = quote.price + price_move if side == "BUY" else max(1, quote.price - price_move)
        turnover = fill * quantity
        pieces, charges = calculate_charges(turnover / 100, side.lower())
        cash = account["cash"]
        realized = None

        if side == "BUY":
            fees = to_paise(charges)
            held = position["quantity"] if position else 0
            combined = held + quantity
            if turnover + fees > cash:
                raise PaperTradeError("Insufficient paper cash for this buy and its estimated fees.")
            if combined * max(fill, quote.price) * 100 > account["capital"] * account["position_pct"]:
                raise PaperTradeError("This buy would exceed the paper position limit for this stock.")
            if not position and db.execute("SELECT COUNT(*) FROM positions").fetchone()[0] >= account["max_positions"]:
                raise PaperTradeError("The paper account has reached its open position limit.")
            stop = min(quote.price - 1, fill * (100 - account["stop_pct"]) // 100)
            if stop < 1:
                raise PaperTradeError("The price is too low for a paper stop below the last trade.")
            if position:
                stop = max(stop, position["stop"])
            cash -= turnover + fees
            cost = (position["cost"] if position else 0) + turnover + fees
            db.execute(
                """INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(symbol) DO UPDATE SET quantity = excluded.quantity,
                   cost = excluded.cost, stop = excluded.stop, last_price = excluded.last_price,
                   quote_time = excluded.quote_time""",
                (symbol, combined, cost, stop, quote.price, quote.at.isoformat()),
            )
        else:
            if db.execute(
                """SELECT 1 FROM trades WHERE symbol = ? AND side = 'SELL'
                   AND substr(executed_at, 1, 10) = ? LIMIT 1""",
                (symbol, now.date().isoformat()),
            ).fetchone():
                charges -= pieces["DP charge"]
            fees = to_paise(charges)
            remaining = position["quantity"] - quantity
            cost_sold = (position["cost"] * quantity + position["quantity"] // 2) // position["quantity"]
            cash += turnover - fees
            realized = turnover - fees - cost_sold
            if remaining:
                db.execute(
                    """UPDATE positions SET quantity = ?, cost = ?, last_price = ?, quote_time = ?
                       WHERE symbol = ?""",
                    (remaining, position["cost"] - cost_sold, quote.price, quote.at.isoformat(), symbol),
                )
            else:
                db.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))

        db.execute("UPDATE account SET cash = ? WHERE id = 1", (cash,))
        db.execute(
            """INSERT INTO trades (executed_at, symbol, side, quantity, quote_price, quote_time,
               fill_price, fees, slippage, realized_pnl, cash_after, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now.isoformat(), symbol, side, quantity, quote.price, quote.at.isoformat(),
             fill, fees, abs(fill - quote.price) * quantity, realized, cash, reason),
        )
        return fill
