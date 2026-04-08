#!/usr/bin/env python3
import json
import random
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import floor
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


HOST = "0.0.0.0"
PORT = 80
DB_PATH = Path(__file__).with_name("trade_log.sqlite3")
ITALY_TZ = ZoneInfo("Europe/Rome")
PAYLOAD_FIELDS = (
    "open_price",
    "stop_loss",
    "take_profit",
    "profit",
    "bid",
    "ask",
)
EVENT_DIFF_FIELDS = (
    "stop_loss",
    "take_profit",
)
CANDLE_PAYLOAD_FIELDS = (
    "open",
    "high",
    "low",
    "close",
)
CANDLE_TIMEFRAMES = {
    "M1": 60,
}
DEMO_COMMAND_PROBABILITY = 1 / 30
DEMO_COMMAND_LOT = 0.01


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def format_timestamp_for_table(value: str) -> str:
    return parse_timestamp(value).astimezone(ITALY_TZ).strftime("%Y-%m-%d %H:%M:%S")


def format_timestamp_for_header(value: str) -> str:
    return f"{format_timestamp_for_table(value)} ora italiana"


def candle_close_time(open_time: int, timeframe: str) -> int:
    try:
        return open_time + CANDLE_TIMEFRAMES[timeframe]
    except KeyError as exc:
        raise ValueError(f"Unsupported candle timeframe '{timeframe}'.") from exc


def decide_demo_command(trades: list[dict]) -> dict:
    if random.random() >= DEMO_COMMAND_PROBABILITY:
        return {"action": "NONE"}
    if trades:
        return {
            "action": "CLOSE",
            "reason": "demo_random_close_when_trade_exists",
        }
    return {
        "action": "OPEN",
        "side": "BUY",
        "lot": DEMO_COMMAND_LOT,
        "reason": "demo_random_open_when_no_trade_exists",
    }


class TradeStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS trade_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_time TEXT NOT NULL,
                    cycle_id TEXT NOT NULL,
                    ticket INTEGER NOT NULL,
                    event_type TEXT NOT NULL CHECK(event_type IN ('OPEN', 'UPDATE', 'CLOSE')),
                    symbol TEXT NOT NULL,
                    open_price REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    profit REAL,
                    bid REAL,
                    ask REAL,
                    changed_fields TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS current_trades (
                    ticket INTEGER PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    open_price REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    profit REAL NOT NULL,
                    bid REAL NOT NULL,
                    ask REAL NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS api_calls (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    received_at TEXT NOT NULL,
                    path TEXT NOT NULL,
                    trade_count INTEGER NOT NULL,
                    remote_addr TEXT,
                    payload_json TEXT NOT NULL,
                    result_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS api_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    path TEXT NOT NULL,
                    remote_addr TEXT,
                    error_message TEXT NOT NULL,
                    payload_text TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_trade_events_time
                ON trade_events(event_time DESC);

                CREATE INDEX IF NOT EXISTS idx_api_errors_time
                ON api_errors(created_at DESC);

                CREATE TABLE IF NOT EXISTS closed_candles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    open_time INTEGER NOT NULL,
                    close_time INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(symbol, timeframe, open_time)
                );

                CREATE TABLE IF NOT EXISTS current_candle_states (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    candle_open_time INTEGER NOT NULL,
                    captured_at TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_closed_candles_lookup
                ON closed_candles(symbol, timeframe, open_time DESC);

                CREATE INDEX IF NOT EXISTS idx_current_candle_states_lookup
                ON current_candle_states(symbol, timeframe, candle_open_time, captured_at DESC);
                """
            )
            connection.commit()

    def ingest_trade_list(self, trades: list[dict]) -> dict:
        normalized = {trade["ticket"]: trade for trade in (normalize_trade(item) for item in trades)}
        cycle_id = str(uuid.uuid4())
        event_time = utc_now()

        with closing(self._connect()) as connection:
            current_rows = connection.execute("SELECT * FROM current_trades").fetchall()
            current = {row["ticket"]: dict(row) for row in current_rows}

            inserted = 0
            updated = 0
            closed = 0

            for ticket in sorted(normalized.keys() - current.keys()):
                trade = normalized[ticket]
                self._insert_event(
                    connection=connection,
                    event_time=event_time,
                    cycle_id=cycle_id,
                    ticket=ticket,
                    event_type="OPEN",
                    trade=trade,
                    changed_fields={field: {"old": None, "new": trade[field]} for field in EVENT_DIFF_FIELDS},
                )
                self._upsert_current_trade(connection, trade, event_time, event_time)
                inserted += 1

            for ticket in sorted(normalized.keys() & current.keys()):
                trade = normalized[ticket]
                previous = current[ticket]
                changed_fields = {}
                for field in EVENT_DIFF_FIELDS:
                    old_value = float(previous[field])
                    new_value = trade[field]
                    if old_value != new_value:
                        changed_fields[field] = {"old": old_value, "new": new_value}
                if not changed_fields:
                    self._upsert_current_trade(connection, trade, previous["opened_at"], event_time)
                    continue
                self._insert_event(
                    connection=connection,
                    event_time=event_time,
                    cycle_id=cycle_id,
                    ticket=ticket,
                    event_type="UPDATE",
                    trade=trade,
                    changed_fields=changed_fields,
                )
                self._upsert_current_trade(connection, trade, previous["opened_at"], event_time)
                updated += 1

            for ticket in sorted(current.keys() - normalized.keys()):
                previous = current[ticket]
                trade = row_to_trade(previous)
                self._insert_event(
                    connection=connection,
                    event_time=event_time,
                    cycle_id=cycle_id,
                    ticket=ticket,
                    event_type="CLOSE",
                    trade=trade,
                    changed_fields={field: {"old": trade[field], "new": None} for field in EVENT_DIFF_FIELDS},
                )
                connection.execute("DELETE FROM current_trades WHERE ticket = ?", (ticket,))
                closed += 1

            connection.commit()
            return {
                "cycle_id": cycle_id,
                "received_trades": len(trades),
                "inserted": inserted,
                "updated": updated,
                "closed": closed,
                "unchanged": len(trades) - inserted - updated,
            }

    def ingest_candles(self, candles: list[dict]) -> dict:
        normalized = [normalize_candle(item) for item in candles]
        closed_candles = sorted(
            (candle for candle in normalized if candle["is_closed"]),
            key=lambda candle: candle["open_time"],
        )
        current_candles = [candle for candle in normalized if not candle["is_closed"]]

        if len(current_candles) > 1:
            raise ValueError("Payload must include at most one current candle.")

        inserted_closed = 0
        inserted_current_states = 0

        with closing(self._connect()) as connection:
            for candle in closed_candles:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO closed_candles (
                        symbol, timeframe, open_time, close_time,
                        open, high, low, close, volume, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candle["symbol"],
                        candle["timeframe"],
                        candle["open_time"],
                        candle_close_time(candle["open_time"], candle["timeframe"]),
                        candle["open"],
                        candle["high"],
                        candle["low"],
                        candle["close"],
                        candle["volume"],
                        json.dumps(candle, sort_keys=True),
                        utc_now(),
                    ),
                )
                inserted_closed += cursor.rowcount

            if current_candles:
                candle = current_candles[0]
                connection.execute(
                    """
                    DELETE FROM current_candle_states
                    WHERE symbol = ? AND timeframe = ? AND candle_open_time != ?
                    """,
                    (candle["symbol"], candle["timeframe"], candle["open_time"]),
                )
                connection.execute(
                    """
                    INSERT INTO current_candle_states (
                        symbol, timeframe, candle_open_time, captured_at,
                        open, high, low, close, volume, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candle["symbol"],
                        candle["timeframe"],
                        candle["open_time"],
                        utc_now(),
                        candle["open"],
                        candle["high"],
                        candle["low"],
                        candle["close"],
                        candle["volume"],
                        json.dumps(candle, sort_keys=True),
                    ),
                )
                inserted_current_states = 1

            connection.commit()

        return {
            "received_candles": len(candles),
            "inserted_closed_candles": inserted_closed,
            "inserted_current_candle_states": inserted_current_states,
        }

    def record_api_call(self, path: str, remote_addr: str, payload: object, result: dict) -> None:
        trade_count = 0
        if isinstance(payload, dict):
            trades = payload.get("trades", [])
            if isinstance(trades, list):
                trade_count = len(trades)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO api_calls (
                    id, received_at, path, trade_count, remote_addr, payload_json, result_json
                ) VALUES (1, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    received_at = excluded.received_at,
                    path = excluded.path,
                    trade_count = excluded.trade_count,
                    remote_addr = excluded.remote_addr,
                    payload_json = excluded.payload_json,
                    result_json = excluded.result_json
                """,
                (
                    utc_now(),
                    path,
                    trade_count,
                    remote_addr,
                    json.dumps(payload, sort_keys=True),
                    json.dumps(result, sort_keys=True),
                ),
            )
            connection.commit()

    def record_api_error(self, path: str, remote_addr: str, payload_text: str, error_message: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO api_errors (
                    created_at, path, remote_addr, error_message, payload_text
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (utc_now(), path, remote_addr, error_message, payload_text),
            )
            connection.commit()

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        event_time: str,
        cycle_id: str,
        ticket: int,
        event_type: str,
        trade: dict,
        changed_fields: dict,
    ) -> None:
        connection.execute(
            """
            INSERT INTO trade_events (
                event_time, cycle_id, ticket, event_type, symbol,
                open_price, stop_loss, take_profit, profit, bid, ask,
                changed_fields, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_time,
                cycle_id,
                ticket,
                event_type,
                trade["symbol"],
                trade["open_price"],
                trade["stop_loss"],
                trade["take_profit"],
                trade["profit"],
                trade["bid"],
                trade["ask"],
                json.dumps(changed_fields, sort_keys=True),
                json.dumps(trade, sort_keys=True),
            ),
        )

    def _upsert_current_trade(
        self,
        connection: sqlite3.Connection,
        trade: dict,
        opened_at: str,
        updated_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO current_trades (
                ticket, symbol, opened_at, updated_at,
                open_price, stop_loss, take_profit, profit, bid, ask, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticket) DO UPDATE SET
                symbol = excluded.symbol,
                opened_at = excluded.opened_at,
                updated_at = excluded.updated_at,
                open_price = excluded.open_price,
                stop_loss = excluded.stop_loss,
                take_profit = excluded.take_profit,
                profit = excluded.profit,
                bid = excluded.bid,
                ask = excluded.ask,
                payload_json = excluded.payload_json
            """,
            (
                trade["ticket"],
                trade["symbol"],
                opened_at,
                updated_at,
                trade["open_price"],
                trade["stop_loss"],
                trade["take_profit"],
                trade["profit"],
                trade["bid"],
                trade["ask"],
                json.dumps(trade, sort_keys=True),
            ),
        )

    def fetch_current_trades(self) -> list[sqlite3.Row]:
        with closing(self._connect()) as connection:
            return connection.execute(
                """
                SELECT ticket, symbol, opened_at, updated_at,
                       open_price, stop_loss, take_profit, profit, bid, ask
                FROM current_trades
                ORDER BY ticket
                """,
            ).fetchall()

    def fetch_recent_events(self, limit: int = 50) -> list[sqlite3.Row]:
        with closing(self._connect()) as connection:
            return connection.execute(
                """
                SELECT event_time, cycle_id, ticket, event_type,
                       open_price, stop_loss, take_profit, profit, bid, ask, changed_fields
                FROM trade_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def fetch_last_api_call(self) -> Optional[sqlite3.Row]:
        with closing(self._connect()) as connection:
            return connection.execute(
                """
                SELECT received_at, path, trade_count, remote_addr, payload_json, result_json
                FROM api_calls
                WHERE id = 1
                """
            ).fetchone()

    def fetch_recent_errors(self, limit: int = 30) -> list[sqlite3.Row]:
        with closing(self._connect()) as connection:
            return connection.execute(
                """
                SELECT id, created_at, path, remote_addr, error_message, payload_text
                FROM api_errors
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def fetch_recent_current_candle_states(self, limit: int = 60) -> list[sqlite3.Row]:
        with closing(self._connect()) as connection:
            latest = connection.execute(
                """
                SELECT symbol, timeframe, candle_open_time
                FROM current_candle_states
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            if latest is None:
                return []
            rows = connection.execute(
                """
                SELECT symbol, timeframe, candle_open_time, captured_at, open, high, low, close, volume
                FROM current_candle_states
                WHERE symbol = ? AND timeframe = ? AND candle_open_time = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (latest["symbol"], latest["timeframe"], latest["candle_open_time"], limit),
            ).fetchall()
            return list(reversed(rows))

    def fetch_recent_closed_candles(self, limit: int = 60) -> list[sqlite3.Row]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT symbol, timeframe, open_time, close_time, open, high, low, close, volume
                FROM closed_candles
                ORDER BY open_time DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return list(reversed(rows))


def row_to_trade(row: dict) -> dict:
    return {
        "ticket": int(row["ticket"]),
        "symbol": row["symbol"],
        "open_price": float(row["open_price"]),
        "stop_loss": float(row["stop_loss"]),
        "take_profit": float(row["take_profit"]),
        "profit": float(row["profit"]),
        "bid": float(row["bid"]),
        "ask": float(row["ask"]),
    }


def normalize_trade(raw_trade: dict) -> dict:
    if not isinstance(raw_trade, dict):
        raise ValueError("Each trade must be an object.")

    symbol = str(raw_trade.get("symbol", ""))

    try:
        ticket = int(raw_trade["ticket"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Trade ticket is required and must be an integer.") from exc

    normalized = {"ticket": ticket, "symbol": symbol}
    for field in PAYLOAD_FIELDS:
        try:
            normalized[field] = float(raw_trade[field])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Trade field '{field}' is required and must be numeric.") from exc
    return normalized


def normalize_candle(raw_candle: dict) -> dict:
    if not isinstance(raw_candle, dict):
        raise ValueError("Each candle must be an object.")

    symbol = str(raw_candle.get("symbol", ""))
    timeframe = str(raw_candle.get("timeframe", ""))

    if not symbol:
        raise ValueError("Candle field 'symbol' is required.")
    if timeframe not in CANDLE_TIMEFRAMES:
        raise ValueError("Candle field 'timeframe' is required and must be supported.")

    try:
        open_time = int(raw_candle["open_time"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Candle field 'open_time' is required and must be an integer.") from exc

    normalized = {
        "symbol": symbol,
        "timeframe": timeframe,
        "open_time": open_time,
    }
    for field in CANDLE_PAYLOAD_FIELDS:
        try:
            normalized[field] = float(raw_candle[field])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Candle field '{field}' is required and must be numeric.") from exc

    try:
        normalized["volume"] = int(raw_candle["volume"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Candle field 'volume' is required and must be an integer.") from exc

    is_closed = raw_candle.get("is_closed")
    if not isinstance(is_closed, bool):
        raise ValueError("Candle field 'is_closed' is required and must be a boolean.")
    normalized["is_closed"] = is_closed
    return normalized


def parse_payload(body: bytes) -> dict:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Request body must be valid JSON.") from exc

    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object.")
    trades = payload.get("trades")
    if trades is None:
        raise ValueError("Payload must include a 'trades' array.")
    if not isinstance(trades, list):
        raise ValueError("'trades' must be an array.")

    candles = payload.get("candles", [])
    if not isinstance(candles, list):
        raise ValueError("'candles' must be an array.")

    return payload


def format_compact_time_from_epoch(value: int) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).astimezone(ITALY_TZ).strftime("%H:%M:%S")


def chart_bounds(values: list[float], padding_ratio: float = 0.08) -> tuple[float, float]:
    minimum = min(values)
    maximum = max(values)
    if minimum == maximum:
        pad = abs(minimum) * padding_ratio if minimum else 1.0
        return minimum - pad, maximum + pad
    pad = (maximum - minimum) * padding_ratio
    return minimum - pad, maximum + pad


def rounded_ten_bounds(values: list[float]) -> tuple[float, float]:
    minimum = min(values)
    maximum = max(values)
    lower = floor(minimum / 10.0) * 10.0
    upper = floor(maximum / 10.0) * 10.0
    if upper < maximum:
        upper += 10.0
    if lower == upper:
        upper += 10.0
    return lower, upper


def polyline_price_chart(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "<p class='meta'>Nessuno stato intra-minuto disponibile.</p>"

    width = 1080
    height = 280
    padding_left = 54
    padding_right = 18
    padding_top = 18
    padding_bottom = 34
    values = [float(row["close"]) for row in rows]
    min_value, max_value = rounded_ten_bounds(values)
    usable_width = width - padding_left - padding_right
    usable_height = height - padding_top - padding_bottom
    step_x = usable_width / max(len(rows) - 1, 1)

    def x_pos(index: int) -> float:
        return padding_left + step_x * index

    def y_pos(value: float) -> float:
        return padding_top + (max_value - value) / (max_value - min_value) * usable_height

    points = " ".join(f"{x_pos(i):.2f},{y_pos(value):.2f}" for i, value in enumerate(values))
    last_price = values[-1]
    last_y = y_pos(last_price)
    label_indexes = sorted({0, len(rows) // 2, len(rows) - 1})
    x_labels = "".join(
        f"<text x='{x_pos(i):.2f}' y='{height - 10}' text-anchor='middle'>{escape(format_timestamp_for_table(rows[i]['captured_at']).split(' ')[1])}</text>"
        for i in label_indexes
    )
    y_labels = []
    tick_count = int((max_value - min_value) / 10.0) + 1
    for tick in range(tick_count):
        value = max_value - tick * 10.0
        ratio = (max_value - value) / (max_value - min_value)
        y = padding_top + usable_height * ratio
        y_labels.append(
            f"<text x='8' y='{y + 4:.2f}'>{format_price(value)}</text>"
            f"<line x1='{padding_left}' y1='{y:.2f}' x2='{width - padding_right}' y2='{y:.2f}' class='chart-grid' />"
        )

    return (
        f"<div class='chart-meta'>Ultimi punti: <strong>{len(rows)}</strong> | Ultimo prezzo: <strong>{format_price(last_price)}</strong></div>"
        f"<svg viewBox='0 0 {width} {height}' class='chart-svg' role='img' aria-label='Grafico ultimo prezzo intra-minuto'>"
        f"<line x1='{padding_left}' y1='{last_y:.2f}' x2='{width - padding_right}' y2='{last_y:.2f}' class='chart-last-line' />"
        f"{''.join(y_labels)}"
        f"<polyline points='{points}' fill='none' stroke='#9c6b1a' stroke-width='3' stroke-linecap='round' stroke-linejoin='round' />"
        f"<circle cx='{x_pos(len(rows) - 1):.2f}' cy='{last_y:.2f}' r='4.5' fill='#9c6b1a' />"
        f"{x_labels}"
        "</svg>"
    )


def candlestick_chart(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "<p class='meta'>Nessuna candela chiusa disponibile.</p>"

    latest_open_time = max(int(row["open_time"]) for row in rows)
    slot_times = [latest_open_time - 60 * offset for offset in range(59, -1, -1)]
    rows_by_open_time = {int(row["open_time"]): row for row in rows}
    slotted_rows = [rows_by_open_time.get(open_time) for open_time in slot_times]

    width = 1080
    height = 320
    padding_left = 54
    padding_right = 18
    padding_top = 18
    padding_bottom = 34
    values = []
    for row in slotted_rows:
        if row is not None:
            values.extend([float(row["high"]), float(row["low"])])
    min_value, max_value = rounded_ten_bounds(values)
    usable_width = width - padding_left - padding_right
    usable_height = height - padding_top - padding_bottom
    candle_slot = usable_width / 60
    candle_width = max(min(candle_slot * 0.58, 10), 3)

    def x_center(index: int) -> float:
        return padding_left + candle_slot * index + candle_slot / 2

    def y_pos(value: float) -> float:
        return padding_top + (max_value - value) / (max_value - min_value) * usable_height

    candle_shapes = []
    for index, row in enumerate(slotted_rows):
        if row is None:
            continue
        center_x = x_center(index)
        open_price = float(row["open"])
        high_price = float(row["high"])
        low_price = float(row["low"])
        close_price = float(row["close"])
        top = y_pos(max(open_price, close_price))
        bottom = y_pos(min(open_price, close_price))
        wick_top = y_pos(high_price)
        wick_bottom = y_pos(low_price)
        color = "#176b3a" if close_price >= open_price else "#9e2f21"
        body_height = max(bottom - top, 1.5)
        candle_shapes.append(
            f"<line x1='{center_x:.2f}' y1='{wick_top:.2f}' x2='{center_x:.2f}' y2='{wick_bottom:.2f}' stroke='{color}' stroke-width='1.5' />"
            f"<rect x='{center_x - candle_width / 2:.2f}' y='{top:.2f}' width='{candle_width:.2f}' height='{body_height:.2f}' fill='{color}' rx='1.5' />"
        )

    label_indexes = list(range(0, 60, 10))
    x_labels = "".join(
        f"<text x='{x_center(i):.2f}' y='{height - 10}' text-anchor='middle'>{escape(format_compact_time_from_epoch(slot_times[i])[:5])}</text>"
        for i in label_indexes
    )
    x_grids = "".join(
        f"<line x1='{x_center(i):.2f}' y1='{padding_top}' x2='{x_center(i):.2f}' y2='{height - padding_bottom}' class='chart-grid chart-grid-vertical' />"
        for i in label_indexes
    )
    y_labels = []
    tick_count = int((max_value - min_value) / 10.0) + 1
    for tick in range(tick_count):
        value = max_value - tick * 10.0
        ratio = (max_value - value) / (max_value - min_value)
        y = padding_top + usable_height * ratio
        y_labels.append(
            f"<text x='8' y='{y + 4:.2f}'>{format_price(value)}</text>"
            f"<line x1='{padding_left}' y1='{y:.2f}' x2='{width - padding_right}' y2='{y:.2f}' class='chart-grid' />"
        )

    latest = rows_by_open_time[latest_open_time]
    shown_count = sum(1 for row in slotted_rows if row is not None)
    return (
        f"<div class='chart-meta'>Finestra: <strong>60 minuti</strong> | Candele presenti: <strong>{shown_count}</strong> | Ultima chiusa: <strong>{format_price(float(latest['close']))}</strong></div>"
        f"<svg viewBox='0 0 {width} {height}' class='chart-svg' role='img' aria-label='Grafico candele chiuse'>"
        f"{''.join(y_labels)}"
        f"{x_grids}"
        f"{''.join(candle_shapes)}"
        f"{x_labels}"
        "</svg>"
    )


def render_dashboard_fragments(store: TradeStore) -> dict[str, str]:
    current_candle_states = store.fetch_recent_current_candle_states()
    recent_closed_candles = store.fetch_recent_closed_candles()
    return {
        "price_chart_html": polyline_price_chart(current_candle_states),
        "candle_chart_html": candlestick_chart(recent_closed_candles),
    }


def render_homepage(store: TradeStore) -> str:
    current_trades = store.fetch_current_trades()
    recent_events = store.fetch_recent_events()
    last_api_call = store.fetch_last_api_call()
    recent_errors = store.fetch_recent_errors()

    trade_rows = []
    for row in current_trades:
        trade_rows.append(
            "<tr>"
            f"<td>{row['ticket']}</td>"
            f"<td>{format_price(row['open_price'])}</td>"
            f"<td>{format_price(row['stop_loss'])}</td>"
            f"<td>{format_price(row['take_profit'])}</td>"
            f"<td>{format_price(row['profit'])}</td>"
            f"<td>{format_price(row['bid'])}</td>"
            f"<td>{format_price(row['ask'])}</td>"
            f"<td>{escape(format_timestamp_for_table(row['updated_at']))}</td>"
            "</tr>"
        )

    event_rows = []
    for row in recent_events:
        event_rows.append(
            "<tr>"
            f"<td>{escape(format_timestamp_for_table(row['event_time']))}</td>"
            f"<td>{escape(row['event_type'])}</td>"
            f"<td>{row['ticket']}</td>"
            f"<td>{format_price(row['stop_loss'])}</td>"
            f"<td>{format_price(row['take_profit'])}</td>"
            f"<td>{format_price(row['profit'])}</td>"
            f"<td>{format_price(row['bid'])}</td>"
            f"<td>{format_price(row['ask'])}</td>"
            "</tr>"
        )

    trade_table = (
        "<table><thead><tr><th>Ticket</th><th>Apertura</th><th>SL</th><th>TP</th>"
        "<th>Profit</th><th>Bid</th><th>Ask</th><th>Ultimo update</th></tr></thead><tbody>"
        + ("".join(trade_rows) if trade_rows else "<tr><td colspan='8'>Nessun trade aperto.</td></tr>")
        + "</tbody></table>"
    )
    event_table = (
        "<table><thead><tr><th>Ora</th><th>Evento</th><th>Ticket</th>"
        "<th>SL</th><th>TP</th><th>Profit</th><th>Bid</th><th>Ask</th></tr></thead><tbody>"
        + ("".join(event_rows) if event_rows else "<tr><td colspan='8'>Nessun evento registrato.</td></tr>")
        + "</tbody></table>"
    )

    if last_api_call:
        last_call_text = escape(format_timestamp_for_header(last_api_call["received_at"]))
    else:
        last_call_text = "Nessuna chiamata ricevuta"

    if recent_errors:
        error_items = []
        for row in recent_errors:
            error_items.append(
                "<details class='error-item'>"
                f"<summary>{escape(format_timestamp_for_table(row['created_at']))} | {escape(row['error_message'])}</summary>"
                "<div class='error-body'>"
                f"<p><strong>Path:</strong> <code>{escape(row['path'])}</code></p>"
                f"<p><strong>Client:</strong> {escape(row['remote_addr'] or '-')}</p>"
                f"<pre>{escape(row['payload_text'])}</pre>"
                "</div>"
                "</details>"
            )
        errors_html = "".join(error_items)
    else:
        errors_html = "<p class='meta'>Nessun errore registrato.</p>"

    dashboard_fragments = render_dashboard_fragments(store)
    price_chart_html = dashboard_fragments["price_chart_html"]
    candle_chart_html = dashboard_fragments["candle_chart_html"]

    return f"""<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MT4 Trade Monitor</title>
  <style>
    :root {{
      --bg: #f6f1e8;
      --panel: #fffaf1;
      --ink: #1d1b19;
      --accent: #9c6b1a;
      --line: #d7c7ad;
      --good: #176b3a;
      --bad: #9e2f21;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(156,107,26,0.12), transparent 30%),
        linear-gradient(180deg, #fbf7f0 0%, var(--bg) 100%);
    }}
    main {{ max-width: 1200px; margin: 0 auto; padding: 32px 20px 48px; }}
    h1, h2 {{ margin: 0 0 12px; }}
    .hero {{
      padding: 24px;
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 18px;
      box-shadow: 0 18px 40px rgba(70, 49, 16, 0.08);
      margin-bottom: 24px;
    }}
    .stack {{ display: grid; gap: 24px; }}
    section {{
      border: 1px solid var(--line);
      background: rgba(255,250,241,0.92);
      border-radius: 18px;
      padding: 20px;
      overflow-x: auto;
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }}
    th {{ color: var(--accent); }}
    code {{ white-space: pre-wrap; word-break: break-word; }}
    pre {{
      margin: 0;
      padding: 12px;
      background: #f3eadb;
      border: 1px solid var(--line);
      border-radius: 12px;
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }}
    .meta {{ color: #5a5247; }}
    .error-item {{
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fff6ec;
      margin-bottom: 12px;
    }}
    .error-item summary {{
      cursor: pointer;
      padding: 12px 14px;
      color: var(--bad);
      font-weight: 700;
    }}
    .error-body {{ padding: 0 14px 14px; }}
    .chart-meta {{
      margin-bottom: 10px;
      color: #5a5247;
      font-size: 14px;
    }}
    .chart-svg {{
      display: block;
      width: 100%;
      min-width: 880px;
      height: auto;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.9), rgba(244,233,216,0.7));
      border: 1px solid var(--line);
      border-radius: 16px;
    }}
    .chart-grid {{
      stroke: rgba(156, 107, 26, 0.16);
      stroke-width: 1;
    }}
    .chart-last-line {{
      stroke: rgba(156, 107, 26, 0.4);
      stroke-width: 1;
      stroke-dasharray: 5 4;
    }}
    svg text {{
      fill: #6b604f;
      font-size: 11px;
      font-family: Georgia, "Times New Roman", serif;
    }}
    .chart-shell {{
      min-height: 140px;
    }}
  </style>
</head>
<body>
  <main>
    <div class="hero">
      <h1>MT4 Trade Monitor</h1>
      <p class="meta">Trade aperti correnti: <strong>{len(current_trades)}</strong></p>
      <p class="meta">Ultima Chiamata {last_call_text}</p>
    </div>
    <div class="stack">
      <section>
        <h2>Trade Aperti</h2>
        {trade_table}
      </section>
      <section>
        <h2>Ultimi Eventi</h2>
        {event_table}
      </section>
      <section>
        <h2>Ultimo Prezzo Intra-Minuto</h2>
        <div id="price-chart-panel" class="chart-shell">
          {price_chart_html}
        </div>
      </section>
      <section>
        <h2>Candele Chiuse</h2>
        <div id="candle-chart-panel" class="chart-shell">
          {candle_chart_html}
        </div>
      </section>
      <section>
        <h2>Errori API</h2>
        {errors_html}
      </section>
    </div>
  </main>
  <script>
    const priceChartPanel = document.getElementById("price-chart-panel");
    const candleChartPanel = document.getElementById("candle-chart-panel");

    async function refreshDashboard() {{
      try {{
        const response = await fetch("/api/dashboard", {{
          headers: {{ "Accept": "application/json" }},
          cache: "no-store"
        }});
        if (!response.ok) {{
          return;
        }}
        const payload = await response.json();
        if (typeof payload.price_chart_html === "string") {{
          priceChartPanel.innerHTML = payload.price_chart_html;
        }}
        if (typeof payload.candle_chart_html === "string") {{
          candleChartPanel.innerHTML = payload.candle_chart_html;
        }}
      }} catch (error) {{
        console.debug("Dashboard refresh failed", error);
      }}
    }}

    window.setInterval(refreshDashboard, 1000);
  </script>
</body>
</html>"""


def format_price(value: float) -> str:
    return f"{float(value):.2f}"


class TradeRequestHandler(BaseHTTPRequestHandler):
    store: Optional[TradeStore] = None

    @classmethod
    def get_store(cls) -> TradeStore:
        if cls.store is None:
            cls.store = TradeStore(DB_PATH)
        return cls.store

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        store = self.get_store()
        if parsed.path == "/api/dashboard":
            self._json_response(HTTPStatus.OK, render_dashboard_fragments(store))
            return
        if parsed.path != "/":
            self._json_response(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        html = render_homepage(store).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        store = self.get_store()
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
        payload_text = body.decode("utf-8", errors="replace")
        remote_addr = self.client_address[0] if self.client_address else ""

        if parsed.path != "/api/trades":
            result = {"error": "Not found"}
            store.record_api_call(parsed.path, remote_addr, {"raw_body": payload_text}, result)
            store.record_api_error(parsed.path, remote_addr, payload_text, "Not found")
            self._json_response(HTTPStatus.NOT_FOUND, result)
            return

        try:
            payload = parse_payload(body)
            trades = payload["trades"]
            candles = payload.get("candles", [])
            result = {
                "trades": store.ingest_trade_list(trades),
                "candles": store.ingest_candles(candles),
                "command": decide_demo_command(trades),
            }
            store.record_api_call(parsed.path, remote_addr, payload, result)
        except ValueError as exc:
            result = {"error": str(exc)}
            store.record_api_call(parsed.path, remote_addr, {"raw_body": payload_text}, result)
            store.record_api_error(parsed.path, remote_addr, payload_text, str(exc))
            self._json_response(HTTPStatus.BAD_REQUEST, result)
            return
        except Exception as exc:
            result = {"error": f"Unexpected error: {exc}"}
            store.record_api_call(parsed.path, remote_addr, {"raw_body": payload_text}, result)
            store.record_api_error(parsed.path, remote_addr, payload_text, f"Unexpected error: {exc}")
            self._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, result)
            return

        self._json_response(HTTPStatus.OK, result)

    def log_message(self, format: str, *args) -> None:
        return

    def _json_response(self, status: HTTPStatus, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), TradeRequestHandler)
    print(f"Listening on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
