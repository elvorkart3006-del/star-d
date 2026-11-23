"""Generate permanent daily research reports from the DC paper ledger."""

import argparse
import hashlib
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from html import escape
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pandas as pd
from kiteconnect.exceptions import KiteException, TokenException
from requests.exceptions import RequestException

import dc_forward as forward
import dc_report_metrics as metrics
import dc_research as research
import multi_stock_scanner as scanner
import paper_trading as paper


REPORT_DIR = forward.JOURNAL_PATH.parent / "dc_daily_reports"
CALENDAR_PATH = REPORT_DIR.parent / "dc_nse_calendar.csv"
SCANNER_CLOSES_FILE = "scanner_closes.csv"
MARKET_SOURCE = "https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf"
LOGGER = logging.getLogger(__name__)
RELIABILITY_FIELDS = (
    "stage", "market", "data_status", "mark_session", "candles_received", "stocks_expected",
    "dc_candles_received", "close_source",
    "missed_trades", "refresh_attempts_recorded", "refresh_errors_recorded", "api_errors_recorded",
    "quote_checks_recorded", "stale_quotes_recorded", "missing_quotes_recorded",
    "all_quotes_fresh_observed",
)
DETAILS = {
    "signals": ("symbol", "session", "direction", "confirmation_close_inr", "observed_at", "status"),
    "positions": ("symbol", "shares", "cost_inr", "mark_inr", "unrealized_pnl_inr"),
    "fills": (
        "symbol", "session", "direction", "filled_at", "quote_at", "shares",
        "quote_price_inr", "fill_price_inr", "fees_inr", "slippage_inr", "realized_pnl_inr",
    ),
    "missed": ("symbol", "signal_session", "direction", "expected_fill_date", "ledger_status_at_generation"),
    "observations": ("observed_at", "source", "status", "fresh_quotes", "stale_quotes", "missing_quotes"),
    "readiness": ("criterion", "target", "progress", "met"),
}


class ReportInputError(ValueError):
    """A saved reporting input needs attention."""


@dataclass(frozen=True)
class ArchivedReport:
    path: Path
    summary: pd.Series
    summary_csv: bytes
    html: bytes
    history: pd.DataFrame
    details: dict[str, pd.DataFrame]


def load_calendar(path=None):
    path = CALENDAR_PATH if path is None else Path(path)
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ReportInputError("Could not read dc_nse_calendar.csv.") from exc
    if not {"date", "status"} <= set(frame):
        raise ReportInputError("dc_nse_calendar.csv needs date and status columns.")
    dates = frame["date"].str.strip()
    statuses = frame["status"].str.strip().str.lower()
    if dates.duplicated().any() or not statuses.isin(("open", "closed")).all():
        raise ReportInputError("Use one open or closed status per date in dc_nse_calendar.csv.")
    if not dates.str.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}").all():
        raise ReportInputError("Use YYYY-MM-DD dates in dc_nse_calendar.csv.")
    try:
        return dict(zip(dates.map(date.fromisoformat), statuses, strict=True))
    except ValueError as exc:
        raise ReportInputError("Use YYYY-MM-DD dates in dc_nse_calendar.csv.") from exc


def load_scanner_closes(path, day):
    """Read an archived scanner export for report valuation only."""
    columns = ["Symbol", "Latest Completed Session", "Latest Close (INR)"]
    try:
        frame = pd.read_csv(path, usecols=columns, dtype=str, keep_default_na=False)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ReportInputError(f"Could not read the saved scanner closes for {day}.") from exc
    rows = frame.loc[frame["Symbol"].isin(forward.SYMBOLS) & frame["Latest Completed Session"].eq(str(day))]
    prices = pd.to_numeric(rows["Latest Close (INR)"], errors="coerce")
    if rows["Symbol"].duplicated().any() or prices.isin([float("inf"), -float("inf")]).any() or not prices.gt(0).all():
        raise ReportInputError(f"The saved scanner closes for {day} contain duplicate or invalid prices.")
    return dict(zip(rows["Symbol"], prices.map(paper.to_paise), strict=True))


def refresh_closes(ledger, through, now, overrides, *, source):
    """Fetch completed candles only. Scheduled reports never submit simulated fills."""
    snapshot = ledger.snapshot()
    if snapshot is None:
        return "Forward test has yet to start"
    first = metrics.day_of(snapshot.run["started_at"])
    if first > through:
        return "Forward test has yet to start on the report date"
    seed = research.load_histories()
    histories = forward.histories_with_closes(seed, snapshot.closes)
    saved_days = {symbol: set(history["date"].dt.date) for symbol, history in histories.items()}
    observed = set().union(*saved_days.values())
    starts = {}
    for symbol, days in saved_days.items():
        # Also look for older gaps inside the enrolled period.
        start = next((
            day for day in metrics.days_between(min(first, max(days) + timedelta(days=1)), through)
            if day not in days and (
                day in observed or not metrics.scheduled_closure(day, overrides)
                or day.weekday() >= 5 and day not in overrides
            )
        ), None)
        if start is not None:
            starts[symbol] = min(start, max(days) + timedelta(days=1))
    if not starts:
        return "Saved data or scheduled closure"
    try:
        kite = scanner.create_kite_client()
        catalog = scanner.get_nse_equity_universe(kite)
        candles = forward.fetch_completed_candles(kite, catalog, starts, through)
        ledger.refresh(seed, candles, {}, paper.now_ist(), source=source)
        return "Updated"
    except scanner.MissingCredentialsError:
        status = "Missing credentials"
    except TokenException:
        status = "Expired or invalid Kite token"
    except RequestException:
        status = "Network error"
    except KiteException:
        status = "Kite API error"
    except ValueError:
        status = "Market data error"
    ledger.record_observation(paper.now_ist(), source, status)
    return status


def report_revisions(root=None, day=None):
    root = REPORT_DIR if root is None else Path(root)
    try:
        dates = [root / str(day)] if day is not None else sorted(root.iterdir(), reverse=True)
    except FileNotFoundError:
        return
    for folder in dates:
        try:
            date.fromisoformat(folder.name)
        except ValueError:
            continue
        try:
            versions = sorted(folder.iterdir(), reverse=True)
        except FileNotFoundError:
            continue
        yield from (path for path in versions if path.name.startswith("revision-") and path.is_dir())


def latest_report(root=None, day=None):
    archived, _ = load_latest_report(root, day)
    return archived.path if archived else None


def load_latest_report(root=None, day=None):
    skipped = False
    for version in report_revisions(root, day):
        try:
            return load_archived_report(version), skipped
        except (OSError, ValueError):
            LOGGER.warning("Could not read DC report revision %s.", version.name, exc_info=True)
            skipped = True
    return None, skipped


def load_archived_report(version):
    """Load a full revision before exposing it in the dashboard or reusing it."""
    summary_csv = (version / "summary.csv").read_bytes()
    summary = pd.read_csv(BytesIO(summary_csv))
    if len(summary) != 1 or not {"date", "stage", "generated_at_ist"} <= set(summary):
        raise ValueError("summary.csv must contain one report row and its generation timestamp.")
    row = summary.iloc[0]
    if row["date"] != version.parent.name or row[["stage", "generated_at_ist"]].isna().any():
        raise ValueError("summary.csv has missing metadata or a mismatched report date.")
    pd.to_numeric(row[[key for key in row.index if key.endswith(("_inr", "_pct"))]])
    html = (version / "report.html").read_bytes()
    if not html.strip():
        raise ValueError("report.html is empty.")
    history = pd.read_csv(version / "history.csv")
    if history.empty or not {"date", "equity_inr", "buy_hold_equity_inr", "drawdown_pct"} <= set(history):
        raise ValueError("history.csv is missing dates or chart columns.")
    history["date"] = pd.to_datetime(history["date"], format="%Y-%m-%d")
    if history["date"].isna().any():
        raise ValueError("history.csv contains a missing date.")
    for key in ("equity_inr", "buy_hold_equity_inr", "drawdown_pct"):
        history[key] = pd.to_numeric(history[key])
    details = {key: pd.read_csv(version / f"{key}.csv") for key in DETAILS}
    return ArchivedReport(version, row, summary_csv, html, history, details)


def detail_frames(report):
    frames = {}
    money = ("confirmation_close", "quote_price", "fill_price", "fees", "slippage", "realized_pnl")
    for key, columns in DETAILS.items():
        rows = []
        for item in getattr(report, key):
            row = dict(item)
            for field in money:
                if field in row:
                    row[field + "_inr"] = row[field] / 100 if row[field] is not None else None
            rows.append(row)
        frames[key] = pd.DataFrame(rows, columns=columns)
    return frames


def _chart(history, columns, title, unit):
    """Render an offline SVG line chart, breaking lines at missing observations."""
    known = [float(row[col]) for row in history for col in columns if row[col] is not None]
    legend = ("DC paper", "Buy-and-hold")
    if not known:
        return f"<h3>{escape(title)}</h3><p>Waiting for recorded values.</p>"
    low, high = min(known), max(known)
    padding = (high - low) * 0.08 or max(0.1, abs(high) * 0.005)
    low, high = low - padding, high + padding
    sx, ex = 72, 675
    top, bottom = 18, 145
    parts = [
        f"<h3>{escape(title)}</h3>",
        f'<svg viewBox="0 0 700 185" role="img" aria-label="{escape(title)}">',
    ]
    for fraction in (0, 0.5, 1):
        y = top + (bottom - top) * fraction
        value = high - (high - low) * fraction
        parts.append(f'<path d="M{sx} {y}H{ex}" stroke="#d6dce5"/>')
        parts.append(f'<text x="{sx-6}" y="{y+4}" text-anchor="end">{value:,.1f}{escape(unit)}</text>')
    for col, color in zip(columns, ("#1d5ab4", "#a14d19")):
        segment = []
        for index, row in enumerate([*history, {col: None}]):
            value = row[col]
            if value is not None:
                x = sx + (ex - sx) * index / max(1, len(history) - 1)
                y = top + (bottom - top) * (high - value) / (high - low)
                segment.append((x, y))
            elif segment:
                points = " ".join(f"{x:.1f},{y:.1f}" for x, y in segment)
                parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>')
                for x, y in segment if len(segment) == 1 else ():
                    parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"/>')
                segment = []
    parts.append(f'<text x="{sx}" y="167">{history[0]["date"]}</text>')
    if len(history) > 1:
        parts.append(f'<text x="{ex}" y="167" text-anchor="end">{history[-1]["date"]}</text>')
    parts.append("</svg>")
    if len(columns) > 1:
        parts.append(f'<p><span style="color:#1d5ab4">● {legend[0]}</span> · <span style="color:#a14d19">● {legend[1]}</span></p>')
    return "".join(parts)


def _table(frame):
    if frame.empty:
        return "<p>No records in this section.</p>"
    return frame.to_html(index=False, border=0, escape=True, na_rep="Unavailable", float_format=lambda n: f"{n:,.2f}")


def render_html(report, history, generated_at, frames):
    row = report.summary
    performance = {key: value for key, value in row.items() if key not in RELIABILITY_FIELDS and key != "date"}
    reliability = {key: row[key] for key in RELIABILITY_FIELDS}

    def key_values(values):
        pretty = {
            key.replace("_pct", " (%)").replace("_inr", " (INR)").replace("_", " ").capitalize():
            "Unavailable" if value is None else value for key, value in values.items()
        }
        return _table(pd.DataFrame(pretty.items(), columns=["Metric", "Value"]))

    sections = (
        ("Readiness progress", "readiness"), ("New DC signals", "signals"), ("Open positions", "positions"),
        ("Simulated fills", "fills"), ("Missed eligible fills", "missed"), ("Recorded refresh attempts", "observations"),
    )
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>DC paper daily report · {row["date"]}</title>'
        '<style>body{font:16px system-ui;max-width:1050px;margin:32px auto;padding:0 20px;color:#172941}'
        'table{border-collapse:collapse;display:block;overflow:auto}th,td{border-bottom:1px solid #d6dce5;'
        'padding:7px 12px;text-align:left;white-space:nowrap}th{background:#edf4fc}'
        'svg{max-width:730px;width:100%;font-size:11px}section{margin:25px 0}</style>'
        f'<h1>DC 5% · PAPER TRADING</h1><h2>Daily performance · {row["date"]}</h2>'
        f'<p>{row["stage"]} · Generated {escape(generated_at)} · {row["market"]}</p>'
        '<p>Values use recorded simulated fills and completed NSE candles. Unavailable values are blank in CSVs. '
        'Saved scanner closes, when listed as a close source, supply report valuations. '
        'Recorded DC signals, fills and monitoring counts come from the forward journal. '
        'A scheduled closure may carry the last fully observed marks, with the mark date shown. '
        'Missing sessions break daily comparisons. No recorded refresh does not identify the reason.</p>'
        '<section><h2>Trading performance</h2>' + key_values(performance) + '</section>'
        '<section><h2>System reliability</h2>' + key_values(reliability) + '</section>'
        '<section><h2>Historical trends</h2>'
        + _chart(history, ("equity_inr", "buy_hold_equity_inr"), "Closing equity (INR)", "")
        + _chart(history, ("drawdown_pct",), "Closing drawdown", "%") + '</section>'
        '<p>Buy-and-hold enters at the first observed trading close after forward enrollment, using the same '
        'seven sleeves, estimated entry charges and slippage. Cash dividends are excluded. '
        'Expectancy is average realized net P/L per closed round trip.</p>'
        + "".join(f"<section><h2>{title}</h2>{_table(frames[key])}</section>" for title, key in sections)
        + f'<p>Calendar: <a href="{MARKET_SOURCE}">NSE holiday circular for 2026</a>. '
        'Weekend scheduling and local calendar overrides are superseded by recorded market evidence.</p></html>'
    )


def save_report(report, history, now, root):
    day = report.summary["date"]
    frames = detail_frames(report)
    canonical = {
        "summary": pd.DataFrame([report.summary]), "history": pd.DataFrame(history), **frames,
    }
    csvs = {name: frame.to_csv(index=False, float_format="%.6f") for name, frame in canonical.items()}
    digest = hashlib.sha256("".join(csvs.values()).encode("utf-8")).hexdigest()
    previous = next(report_revisions(root, day), None)
    if previous is not None:
        try:
            if (previous / "digest.txt").read_text(encoding="ascii").strip() == digest:
                load_archived_report(previous)
                return previous
        except (OSError, ValueError):
            LOGGER.warning("Could not reuse DC report revision %s.", previous.name, exc_info=True)

    folder = root / day
    folder.mkdir(parents=True, exist_ok=True)
    suffix = uuid4().hex[:8]
    version = folder / f"revision-{now:%Y%m%dT%H%M%S%f}-{suffix}"
    generated_at = now.isoformat(timespec="seconds")
    csvs["summary"] = canonical["summary"].assign(generated_at_ist=generated_at).to_csv(index=False, float_format="%.6f")
    # Use the archive's inherited permissions, including after the Windows rename.
    staging = folder / f".writing-{suffix}"
    staging.mkdir()
    try:
        for name, content in csvs.items():
            (staging / f"{name}.csv").write_text(content, encoding="utf-8", newline="")
        (staging / "report.html").write_text(render_html(report, history, generated_at, frames), encoding="utf-8")
        (staging / "digest.txt").write_text(digest, encoding="ascii")
        staging.rename(version)
    except BaseException:
        try:
            for file in staging.iterdir():
                file.unlink()
            staging.rmdir()
        except OSError:
            LOGGER.warning("Could not clean up DC report staging folder %s.", staging.name, exc_info=True)
        raise
    return version


def generate_reports(through, now, *, ledger=None, output=None, catch_up=False, overrides=None):
    ledger = forward.ForwardLedger() if ledger is None else ledger
    output = REPORT_DIR if output is None else Path(output)
    overrides = load_calendar() if overrides is None else overrides
    snapshot = ledger.snapshot()
    first = metrics.day_of(snapshot.run["started_at"]) if snapshot else through
    report_closes = {
        day: load_scanner_closes(output / day.isoformat() / SCANNER_CLOSES_FILE, day)
        for day in metrics.days_between(first, through)
    }
    reports = metrics.build_reports(
        snapshot, through, now, overrides=overrides, report_closes=report_closes,
        observations=ledger.observations() if not snapshot else (),
    )
    history = []
    for day, report in reports.items():
        history.append(report.summary)
        if catch_up or day == through:
            latest = save_report(report, history, now, output)
    return latest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, help="Report date in India, including today's interim report.")
    parser.add_argument("--catch-up", action="store_true", help="Fill missing report dates since forward enrollment.")
    parser.add_argument("--refresh", action="store_true", help="Try Kite historical candles before saving. Paper fills stay manual.")
    parser.add_argument("--scheduled", action="store_true", help="Label a Windows scheduled execution.")
    args = parser.parse_args(argv)
    now = paper.now_ist()
    completed = now.date() if now.time() >= metrics.REPORT_AFTER else now.date() - timedelta(days=1)
    target = args.date or completed
    if target > now.date():
        parser.error("Report dates cannot be in the future.")
    ledger = forward.ForwardLedger()
    overrides = load_calendar()
    if args.refresh:
        status = refresh_closes(
            ledger, min(target, completed), now, overrides, source="Windows schedule" if args.scheduled else "Report CLI",
        )
        print(f"Completed candle refresh: {status}")
        if status == "Expired or invalid Kite token":
            print("Refresh .env with the existing kite_callback.py login flow, then rerun the report.")
    path = generate_reports(target, paper.now_ist(), ledger=ledger, catch_up=args.catch_up, overrides=overrides)
    print(f"Paper report saved: {path / 'report.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
