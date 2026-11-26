"""Offline-only shadow executor for synthetic DC 5% test data.

This prototype has no Kite, broker, network, credential, SQLite, or file-writing
integration. It turns a confirmed DC signal into an in-memory virtual intent and
applies the existing simulated cost assumptions to an in-memory virtual ledger.
It is deliberately not connected to the live dashboard or forward journal.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

import backtest_with_costs as cost_model


SYMBOLS = ("INFY", "TCS", "WIPRO", "HDFCBANK", "ICICIBANK", "SBIN", "ITC")
DC_THRESHOLD = 0.05
ACCOUNT_CAPITAL_INR = 70_000
SLEEVE_CAPITAL_INR = ACCOUNT_CAPITAL_INR // len(SYMBOLS)
MAX_QUOTE_AGE = timedelta(minutes=5)
NSE_OPEN = time(9, 15)
NSE_CLOSE = time(15, 30)
DAILY_MARK_TIME = time(16, 0)
IST = ZoneInfo("Asia/Kolkata")
SYNTHETIC_SOURCE = "SYNTHETIC"


class ShadowInputError(ValueError):
    """Input was invalid or did not come from the synthetic test source."""


@dataclass(frozen=True)
class CompletedClose:
    symbol: str
    session: date
    close_inr: float
    source: str = SYNTHETIC_SOURCE


@dataclass(frozen=True)
class QuoteSnapshot:
    symbol: str
    price_inr: float
    last_trade_at: datetime
    previous_close_inr: float
    source: str = SYNTHETIC_SOURCE


@dataclass(frozen=True)
class DCConfirmation:
    direction: str
    session: date
    close_inr: float
    extreme_session: date
    extreme_close_inr: float


@dataclass
class ShadowSignal:
    symbol: str
    confirmation: DCConfirmation
    status: str = "Pending"
    last_check: str = "Waiting for a synthetic next-session quote"


@dataclass(frozen=True)
class ShadowOrderIntent:
    symbol: str
    side: str
    quantity: int
    signal_session: date
    quote_price_inr: float
    created_at: datetime
    mode: str = "SHADOW_ONLY"


@dataclass(frozen=True)
class VirtualFill:
    symbol: str
    side: str
    quantity: int
    signal_session: date
    quote_time: datetime
    quote_price_inr: float
    fill_price_inr: float
    estimated_fees_inr: float
    estimated_slippage_inr: float
    realized_pnl_inr: float | None
    mode: str = "VIRTUAL"


@dataclass
class _Sleeve:
    cash_paise: int
    shares: int = 0
    basis_paise: int = 0
    mark_paise: int = 0
    mark_at: datetime | None = None


@dataclass
class InMemoryVirtualLedger:
    """Seven independent INR 10,000 sleeves held only in process memory."""

    sleeves: dict[str, _Sleeve] = field(default_factory=lambda: {
        symbol: _Sleeve(_to_paise(SLEEVE_CAPITAL_INR)) for symbol in SYMBOLS
    })
    signals: list[ShadowSignal] = field(default_factory=list)
    intents: list[ShadowOrderIntent] = field(default_factory=list)
    fills: list[VirtualFill] = field(default_factory=list)

    @property
    def starting_capital_inr(self):
        return ACCOUNT_CAPITAL_INR

    @property
    def cash_inr(self):
        return sum(sleeve.cash_paise for sleeve in self.sleeves.values()) / 100

    @property
    def market_value_inr(self):
        return sum(sleeve.shares * sleeve.mark_paise for sleeve in self.sleeves.values()) / 100

    @property
    def portfolio_value_inr(self):
        return self.cash_inr + self.market_value_inr


@dataclass(frozen=True)
class ExecutionOutcome:
    filled: bool
    reason: str
    intent: ShadowOrderIntent | None = None
    fill: VirtualFill | None = None


def _to_paise(amount):
    try:
        value = Decimal(str(amount))
        if not value.is_finite() or value < 0:
            raise InvalidOperation
        return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError) as error:
        raise ShadowInputError("Enter a finite, non-negative INR amount.") from error


def directional_changes(closes, threshold=DC_THRESHOLD):
    """Return close-confirmed DC events using the project's fixed 5% rule."""
    if not 0 < threshold < 1:
        raise ShadowInputError("The DC threshold must be between zero and one.")
    change = Decimal(str(threshold))
    up_factor = Decimal("1") + change
    down_factor = Decimal("1") - change
    high = low = None
    trend = None
    events = []
    for session, close_value in closes:
        close = Decimal(str(close_value))
        if not close.is_finite() or close <= 0:
            raise ShadowInputError("Completed closes must be positive finite prices.")
        current = (session, close)
        if high is None:
            high = low = current
        elif trend != "up" and close >= low[1] * up_factor:
            events.append(DCConfirmation("up", session, float(close), low[0], float(low[1])))
            trend, high = "up", current
        elif trend != "down" and close <= high[1] * down_factor:
            events.append(DCConfirmation("down", session, float(close), high[0], float(high[1])))
            trend, low = "down", current
        else:
            if trend != "down" and close > high[1]:
                high = current
            if trend != "up" and close < low[1]:
                low = current
    return events


class ShadowExecutionWrapper:
    """Validate synthetic signals and create virtual fills without a broker."""

    def __init__(self):
        self.ledger = InMemoryVirtualLedger()
        self._closes = {symbol: [] for symbol in SYMBOLS}
        self._last_close = {}

    def observe_completed_close(self, candle: CompletedClose):
        self._require_synthetic(candle.source)
        if candle.symbol not in self.ledger.sleeves:
            raise ShadowInputError("The symbol is not part of the fixed DC test universe.")
        if candle.close_inr <= 0:
            raise ShadowInputError("A completed close must be greater than zero.")
        prior = self._last_close.get(candle.symbol)
        if prior and candle.session <= prior[0]:
            raise ShadowInputError("Completed sessions must arrive once in ascending order.")

        for signal in self.ledger.signals:
            if (
                signal.symbol == candle.symbol
                and signal.status == "Pending"
                and signal.confirmation.session < candle.session
            ):
                signal.status = "Expired"
                signal.last_check = "A later completed close arrived before an eligible quote"

        self._closes[candle.symbol].append((candle.session, candle.close_inr))
        self._last_close[candle.symbol] = (candle.session, candle.close_inr)
        sleeve = self.ledger.sleeves[candle.symbol]
        sleeve.mark_paise = _to_paise(candle.close_inr)
        sleeve.mark_at = datetime.combine(candle.session, DAILY_MARK_TIME, IST)

        events = directional_changes(self._closes[candle.symbol])
        if events and events[-1].session == candle.session:
            confirmation = events[-1]
            signal = ShadowSignal(candle.symbol, confirmation)
            self.ledger.signals.append(signal)
            return signal
        return None

    def observe_quote(self, quote: QuoteSnapshot, now: datetime):
        """Evaluate one synthetic quote; every successful action stays virtual."""
        self._require_synthetic(quote.source)
        if quote.symbol not in self.ledger.sleeves:
            raise ShadowInputError("The symbol is not part of the fixed DC test universe.")
        if quote.price_inr <= 0:
            return ExecutionOutcome(False, "Quote price must be positive.")
        if quote.symbol not in self._last_close:
            return ExecutionOutcome(False, "No completed close is available for this symbol.")

        signal = next(
            (
                item for item in reversed(self.ledger.signals)
                if item.symbol == quote.symbol and item.status == "Pending"
            ),
            None,
        )
        if signal is None:
            return ExecutionOutcome(False, "No pending DC confirmation.")

        now_ist = _as_ist(now)
        trade_at = _as_ist(quote.last_trade_at)
        fresh, freshness_reason = _quote_freshness(trade_at, now_ist)
        signal.last_check = freshness_reason
        if not fresh:
            return ExecutionOutcome(False, freshness_reason)

        sleeve = self.ledger.sleeves[quote.symbol]
        trade_paise = _to_paise(quote.price_inr)
        if sleeve.mark_at is None or trade_at > sleeve.mark_at:
            sleeve.mark_paise = trade_paise
            sleeve.mark_at = trade_at

        last_session, last_close_inr = self._last_close[quote.symbol]
        if _to_paise(quote.previous_close_inr) != _to_paise(last_close_inr):
            signal.last_check = "Quote previous close does not match the latest completed close."
            return ExecutionOutcome(False, signal.last_check)
        if trade_at.date() <= signal.confirmation.session:
            signal.last_check = "A quote after the signal session is required."
            return ExecutionOutcome(False, signal.last_check)
        if trade_at < sleeve.mark_at:
            signal.last_check = "Quote timestamp is older than the last completed-close mark."
            return ExecutionOutcome(False, signal.last_check)
        if last_session != signal.confirmation.session:
            signal.last_check = "A newer completed session has superseded this signal."
            return ExecutionOutcome(False, signal.last_check)

        is_buy = signal.confirmation.direction == "up"
        if is_buy and sleeve.shares:
            signal.status = "Already long"
            return ExecutionOutcome(False, signal.status)
        if not is_buy and not sleeve.shares:
            signal.status = "Flat"
            return ExecutionOutcome(False, signal.status)

        side = "BUY" if is_buy else "SELL"
        rate = cost_model.SLIPPAGE_RATE if is_buy else -cost_model.SLIPPAGE_RATE
        fill_paise = max(1, _to_paise(quote.price_inr * (1 + rate)))
        quantity = sleeve.cash_paise // fill_paise if is_buy else sleeve.shares
        fee_paise = 0
        while quantity > 0:
            turnover_paise = fill_paise * quantity
            _, charges_inr = cost_model.calculate_charges(turnover_paise / 100, side.lower())
            fee_paise = _to_paise(charges_inr)
            if not is_buy or turnover_paise + fee_paise <= sleeve.cash_paise:
                break
            quantity -= 1
        if quantity <= 0:
            signal.status = "Insufficient cash"
            return ExecutionOutcome(False, signal.status)

        intent = ShadowOrderIntent(
            quote.symbol,
            side,
            quantity,
            signal.confirmation.session,
            quote.price_inr,
            trade_at,
        )
        turnover_paise = fill_paise * quantity
        realized = None
        if is_buy:
            sleeve.cash_paise -= turnover_paise + fee_paise
            sleeve.shares = quantity
            sleeve.basis_paise = turnover_paise + fee_paise
        else:
            proceeds_paise = turnover_paise - fee_paise
            realized = (proceeds_paise - sleeve.basis_paise) / 100
            sleeve.cash_paise += proceeds_paise
            sleeve.shares = 0
            sleeve.basis_paise = 0

        fill = VirtualFill(
            symbol=quote.symbol,
            side=side,
            quantity=quantity,
            signal_session=signal.confirmation.session,
            quote_time=trade_at,
            quote_price_inr=quote.price_inr,
            fill_price_inr=fill_paise / 100,
            estimated_fees_inr=fee_paise / 100,
            estimated_slippage_inr=abs(fill_paise - trade_paise) * quantity / 100,
            realized_pnl_inr=realized,
        )
        self.ledger.intents.append(intent)
        self.ledger.fills.append(fill)
        signal.status = "Shadow filled"
        signal.last_check = "Synthetic quote passed checks; virtual ledger updated only."
        return ExecutionOutcome(True, signal.last_check, intent, fill)

    @staticmethod
    def _require_synthetic(source):
        if source != SYNTHETIC_SOURCE:
            raise ShadowInputError("This isolated prototype accepts synthetic data only.")


def _as_ist(moment):
    if moment.tzinfo is None:
        return moment.replace(tzinfo=IST)
    return moment.astimezone(IST)


def _quote_freshness(trade_at, now):
    if not (NSE_OPEN <= now.time() < NSE_CLOSE):
        return False, "Current time is outside NSE market hours."
    if not (NSE_OPEN <= trade_at.time() < NSE_CLOSE):
        return False, "Last trade timestamp is outside NSE market hours."
    if trade_at.date() != now.date():
        return False, "Last trade timestamp is not from the current session."
    age = now - trade_at
    if age < timedelta(0):
        return False, "Last trade timestamp is in the future."
    if age > MAX_QUOTE_AGE:
        return False, "Last trade timestamp is more than five minutes old."
    return True, "Synthetic quote is fresh."


def synthetic_demo():
    """Return a small in-memory example; it never writes to disk or contacts a service."""
    wrapper = ShadowExecutionWrapper()
    for session, close in (
        (date(2026, 11, 18), 100.0),
        (date(2026, 11, 19), 94.0),
        (date(2026, 11, 20), 99.0),
    ):
        wrapper.observe_completed_close(CompletedClose("TCS", session, close))
    now = datetime(2026, 11, 23, 10, 1, tzinfo=IST)
    result = wrapper.observe_quote(
        QuoteSnapshot("TCS", 99.25, datetime(2026, 11, 23, 10, 0, tzinfo=IST), 99.0),
        now,
    )
    return wrapper, result


if __name__ == "__main__":
    demo, outcome = synthetic_demo()
    print("OFFLINE SHADOW DEMO - SYNTHETIC INPUTS ONLY")
    print("Virtual fill created:", outcome.filled)
    if outcome.fill:
        print(
            f"{outcome.fill.side} {outcome.fill.quantity} TCS share(s) at "
            f"INR {outcome.fill.fill_price_inr:.2f}; mode={outcome.fill.mode}"
        )
    print(f"Virtual account value: INR {demo.ledger.portfolio_value_inr:.2f}")
