"""Persistent DC 5% forward simulation using completed closes and live quotes."""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

import backtest_sma_6_30_kite as kite_batch
import backtest_with_costs as backtest
import dc_research as research
import multi_stock_scanner as scanner
import paper_trading as paper


SYMBOLS = kite_batch.SYMBOLS
JOURNAL_PATH = backtest.DATA_DIR / "dc_forward.sqlite3"
SLEEVE_CAPITAL = paper.to_paise(backtest.STARTING_CAPITAL)
TOTAL_CAPITAL = SLEEVE_CAPITAL * len(SYMBOLS)
DAILY_CUTOFF = time(16)
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS run (
        id INTEGER PRIMARY KEY CHECK (id = 1), started_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, peak INTEGER NOT NULL, max_drawdown REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sleeves (
        symbol TEXT PRIMARY KEY, cash INTEGER NOT NULL, shares INTEGER NOT NULL,
        basis INTEGER NOT NULL, mark INTEGER NOT NULL, mark_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS closes (
        symbol TEXT NOT NULL, session TEXT NOT NULL, price INTEGER NOT NULL,
        PRIMARY KEY (symbol, session)
    )""",
    """CREATE TABLE IF NOT EXISTS signals (
        symbol TEXT NOT NULL, session TEXT NOT NULL, direction TEXT NOT NULL,
        confirmation_close INTEGER NOT NULL, observed_at TEXT NOT NULL, status TEXT NOT NULL,
        filled_at TEXT, quote_at TEXT, quote_price INTEGER, fill_price INTEGER,
        shares INTEGER, fees INTEGER, slippage INTEGER, realized_pnl INTEGER,
        PRIMARY KEY (symbol, session)
    )""",
    """CREATE TABLE IF NOT EXISTS observations (
        id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, source TEXT NOT NULL,
        status TEXT NOT NULL, fresh_quotes INTEGER, stale_quotes INTEGER, missing_quotes INTEGER
    )""",
)


@dataclass(frozen=True)
class MarketQuote:
    trade: paper.Quote
    previous_close: int


@dataclass(frozen=True)
class Snapshot:
    run: sqlite3.Row
    sleeves: list
    closes: list
    signals: list
    observations: tuple = ()

    @property
    def fills(self):
        return [row for row in self.signals if row["status"] == "Filled"]


def completed_cutoff(now):
    """Wait until 16:00 IST to use the current day's daily candle."""
    return now.date() if now.time() >= DAILY_CUTOFF else now.date() - timedelta(days=1)


def histories_with_closes(seed, closes=()):
    """Replay the fixed DC rule on the saved history and new completed closes."""
    by_symbol = {symbol: [] for symbol in SYMBOLS}
    for row in closes:
        by_symbol[row["symbol"]].append((pd.Timestamp(row["session"]), row["price"] / 100))
    histories = {}
    for symbol in SYMBOLS:
        history = seed[symbol][["date", "close"]]
        if by_symbol[symbol]:
            extras = pd.DataFrame(by_symbol[symbol], columns=["date", "close"])
            history = pd.concat([history, extras], ignore_index=True)
            history = history.drop_duplicates("date", keep="last").sort_values("date", ignore_index=True)
        histories[symbol] = history
    return histories


def latest_signals(histories):
    """Show today's confirmation separately from the last confirmed direction."""
    rows = []
    for symbol, history in histories.items():
        last_day = history["date"].iloc[-1].date()
        events = list(research.directional_changes(history))
        last = events[-1] if events else None
        trend = {"up": "Bullish", "down": "Bearish"}[last.direction] if last else "Unconfirmed"
        rows.append({
            "Symbol": symbol,
            "Completed session": last_day,
            "Daily DC signal": trend if last and last.day.date() == last_day else "None",
            "DC trend": trend,
            "Last confirmation": last.day.date() if last else None,
            "Close (INR)": float(history["close"].iloc[-1]),
        })
    return pd.DataFrame(rows)


def fetch_completed_candles(kite, catalog, starts, cutoff):
    """Shared, paced historical-only refresh for the dashboard and reports."""
    tokens = {item["symbol"]: item["instrument_token"] for item in catalog}
    if not starts.keys() <= tokens.keys():
        raise ValueError("The Kite catalog is missing a DC research stock.")
    candles = {}
    for symbol, start in starts.items():
        rows = []
        while start <= cutoff:
            end = min(start + timedelta(days=1999), cutoff)
            rows.extend(scanner._call_with_rate_limit(
                lambda: kite.historical_data(
                    instrument_token=tokens[symbol],
                    from_date=datetime.combine(start, time.min),
                    to_date=datetime.combine(end, time(23, 59, 59)),
                    interval="day",
                ),
                "historical",
            ))
            start = end + timedelta(days=1)
        candles[symbol] = scanner._prepare_candles(rows, starts[symbol], cutoff)
    return candles


class ForwardLedger:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else JOURNAL_PATH

    @contextmanager
    def _database(self, write=False):
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=5)
        else:
            db = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                if write:
                    for statement in SCHEMA:
                        db.execute(statement)
                yield db
        finally:
            db.close()

    def snapshot(self):
        if not self.path.is_file():
            return None
        with self._database() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'run'").fetchone() is None:
                return None
            run = db.execute("SELECT * FROM run WHERE id = 1").fetchone()
            if run is None:
                return None
            return Snapshot(
                run,
                db.execute("SELECT * FROM sleeves ORDER BY symbol").fetchall(),
                db.execute("SELECT * FROM closes ORDER BY session").fetchall(),
                db.execute("SELECT * FROM signals ORDER BY session DESC, symbol").fetchall(),
                self._read_observations(db),
            )

    @staticmethod
    def _read_observations(db):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'observations'").fetchone():
            return ()
        return tuple(db.execute("SELECT * FROM observations ORDER BY observed_at, id"))

    def observations(self):
        if not self.path.is_file():
            return ()
        with self._database() as db:
            return self._read_observations(db)

    def record_observation(self, now, source, status):
        """Record failures without changing the paper portfolio."""
        with self._database(write=True) as db:
            db.execute(
                "INSERT INTO observations (observed_at, source, status) VALUES (?, ?, ?)",
                (now.isoformat(), source, status),
            )

    def refresh(self, seed, candles, quotes, now, *, source=None, quotes_requested=False):
        """Update the forward journal and sleeves atomically using one new observation."""
        cutoff = completed_cutoff(now)
        observed = now.isoformat()
        with self._database(write=True) as db:
            run = db.execute("SELECT * FROM run WHERE id = 1").fetchone()
            if run is not None and observed <= run["updated_at"]:
                return 0
            started = date.fromisoformat((run["started_at"] if run else observed)[:10])
            peak = run["peak"] if run else TOTAL_CAPITAL
            max_drawdown = run["max_drawdown"] if run else 0.0
            db.executemany(
                "INSERT OR IGNORE INTO sleeves VALUES (?, ?, 0, 0, 0, '')",
                ((symbol, SLEEVE_CAPITAL) for symbol in SYMBOLS),
            )

            for symbol, bars in candles.items():
                for row in bars.itertuples(index=False):
                    day = pd.Timestamp(row.date).date()
                    if day <= cutoff:
                        price = paper.to_paise(row.close)
                        if price > 0:
                            db.execute("INSERT OR IGNORE INTO closes VALUES (?, ?, ?)", (symbol, day.isoformat(), price))

            histories = histories_with_closes(seed, db.execute("SELECT * FROM closes"))
            histories = {
                symbol: history.loc[history["date"].dt.date <= cutoff]
                for symbol, history in histories.items()
            }
            latest = max(history["date"].iloc[-1].date() for history in histories.values())
            filled = 0
            for symbol, history in histories.items():
                self._record_signals(db, symbol, history, started, observed)
                close = history.iloc[-1]
                day = close["date"].date()
                self._mark(db, symbol, paper.to_paise(close["close"]), datetime.combine(day, DAILY_CUTOFF, paper.IST))
                market = quotes.get(symbol)
                if market and market.trade.price > 0 and market.trade.is_fresh(now):
                    self._mark(db, symbol, market.trade.price, market.trade.at)
                    if day == latest and day < now.date() and market.previous_close == paper.to_paise(close["close"]):
                        filled += self._fill_pending(db, symbol, day.isoformat(), market.trade, observed)

            db.execute(
                "UPDATE signals SET status = 'Expired' WHERE status = 'Pending' AND session < ?",
                (latest.isoformat(),),
            )
            equity = db.execute("SELECT SUM(cash + shares * mark) FROM sleeves").fetchone()[0]
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
            db.execute(
                """INSERT INTO run VALUES (1, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE
                   SET updated_at = excluded.updated_at, peak = excluded.peak,
                   max_drawdown = excluded.max_drawdown""",
                (run["started_at"] if run else observed, observed, peak, max_drawdown),
            )
            if source:
                fresh = stale = missing = None
                if quotes_requested:
                    observed_quotes = [quotes[symbol].trade for symbol in SYMBOLS if symbol in quotes]
                    fresh = sum(trade.price > 0 and trade.is_fresh(now) for trade in observed_quotes)
                    stale = len(observed_quotes) - fresh
                    missing = len(SYMBOLS) - len(observed_quotes)
                db.execute(
                    """INSERT INTO observations
                       (observed_at, source, status, fresh_quotes, stale_quotes, missing_quotes)
                       VALUES (?, ?, 'Updated', ?, ?, ?)""",
                    (observed, source, fresh, stale, missing),
                )
        return filled

    @staticmethod
    def _record_signals(db, symbol, history, started, observed):
        db.executemany(
            """INSERT OR IGNORE INTO signals
               (symbol, session, direction, confirmation_close, observed_at, status)
               VALUES (?, ?, ?, ?, ?, 'Pending')""",
            (
                (symbol, event.day.date().isoformat(), event.direction, paper.to_paise(event.close), observed)
                for event in research.directional_changes(history)
                if event.day.date() >= started
            ),
        )

    @staticmethod
    def _mark(db, symbol, price, at):
        timestamp = at.isoformat()
        db.execute(
            "UPDATE sleeves SET mark = ?, mark_at = ? WHERE symbol = ? AND mark_at < ?",
            (price, timestamp, symbol, timestamp),
        )

    @staticmethod
    def _fill_pending(db, symbol, session, quote, observed):
        event = db.execute(
            "SELECT * FROM signals WHERE symbol = ? AND session = ? AND status = 'Pending'",
            (symbol, session),
        ).fetchone()
        if event is None or quote.at.date().isoformat() <= session:
            return 0
        sleeve = db.execute("SELECT * FROM sleeves WHERE symbol = ?", (symbol,)).fetchone()
        if quote.at.isoformat() < sleeve["mark_at"]:
            return 0
        buy = event["direction"] == "up"
        shares = sleeve["shares"]
        if (buy and shares) or (not buy and not shares):
            db.execute(
                "UPDATE signals SET status = ? WHERE symbol = ? AND session = ?",
                ("Already long" if buy else "Flat", symbol, session),
            )
            return 0
        cash = sleeve["cash"]
        rate = backtest.SLIPPAGE_RATE if buy else -backtest.SLIPPAGE_RATE
        fill = max(1, paper.to_paise(quote.price / 100 * (1 + rate)))
        quantity = cash // fill if buy else shares
        side = "buy" if buy else "sell"
        while quantity > 0:
            turnover = fill * quantity
            _, estimate = backtest.calculate_charges(turnover / 100, side)
            fees = paper.to_paise(estimate)
            if not buy or turnover + fees <= cash:
                break
            quantity -= 1
        if quantity <= 0:
            db.execute(
                "UPDATE signals SET status = 'Insufficient cash' WHERE symbol = ? AND session = ?",
                (symbol, session),
            )
            return 0
        if buy:
            basis = turnover + fees
            cash -= basis
            realized = None
            held = quantity
        else:
            proceeds = turnover - fees
            cash += proceeds
            realized = proceeds - sleeve["basis"]
            basis = held = 0
        db.execute(
            "UPDATE sleeves SET cash = ?, shares = ?, basis = ? WHERE symbol = ?",
            (cash, held, basis, symbol),
        )
        db.execute(
            """UPDATE signals SET status = 'Filled', filled_at = ?, quote_at = ?, quote_price = ?,
               fill_price = ?, shares = ?, fees = ?, slippage = ?, realized_pnl = ?
               WHERE symbol = ? AND session = ?""",
            (observed, quote.at.isoformat(), quote.price, fill, quantity, fees,
             abs(fill - quote.price) * quantity, realized, symbol, session),
        )
        return 1
