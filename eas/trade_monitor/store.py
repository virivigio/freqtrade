import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Optional

from trade_monitor.core import (
    EVENT_DIFF_FIELDS,
    candle_close_time,
    normalize_candle,
    normalize_trade,
    utc_now,
)


def row_to_trade(row: dict) -> dict:
    return {
        "ticket": int(row["ticket"]),
        "symbol": row["symbol"],
        "side": row["side"],
        "open_price": float(row["open_price"]),
        "stop_loss": float(row["stop_loss"]),
        "take_profit": float(row["take_profit"]),
        "profit": float(row["profit"]),
        "bid": float(row["bid"]),
        "ask": float(row["ask"]),
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
                    side TEXT NOT NULL DEFAULT 'BUY' CHECK(side IN ('BUY', 'SELL')),
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
                    side TEXT NOT NULL DEFAULT 'BUY' CHECK(side IN ('BUY', 'SELL')),
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

                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

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
            connection.execute(
                """
                INSERT OR IGNORE INTO app_state (key, value, updated_at)
                VALUES ('commands_enabled', '0', ?)
                """,
                (utc_now(),),
            )
            self._ensure_trade_side_columns(connection)
            connection.commit()

    def _ensure_trade_side_columns(self, connection: sqlite3.Connection) -> None:
        trade_event_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(trade_events)").fetchall()
        }
        if "side" not in trade_event_columns:
            connection.execute(
                "ALTER TABLE trade_events ADD COLUMN side TEXT NOT NULL DEFAULT 'BUY' CHECK(side IN ('BUY', 'SELL'))"
            )

        current_trade_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(current_trades)").fetchall()
        }
        if "side" not in current_trade_columns:
            connection.execute(
                "ALTER TABLE current_trades ADD COLUMN side TEXT NOT NULL DEFAULT 'BUY' CHECK(side IN ('BUY', 'SELL'))"
            )

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

    def get_commands_enabled(self) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT value FROM app_state WHERE key = 'commands_enabled'"
            ).fetchone()
            return row is not None and row["value"] == "1"

    def set_commands_enabled(self, enabled: bool) -> bool:
        value = "1" if enabled else "0"
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO app_state (key, value, updated_at)
                VALUES ('commands_enabled', ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (value, utc_now()),
            )
            connection.commit()
        return enabled

    def fetch_current_trades(self) -> list[sqlite3.Row]:
        with closing(self._connect()) as connection:
            return connection.execute(
                """
                SELECT ticket, symbol, opened_at, updated_at,
                       side, open_price, stop_loss, take_profit, profit, bid, ask
                FROM current_trades
                ORDER BY ticket
                """,
            ).fetchall()

    def fetch_current_trade(self) -> Optional[sqlite3.Row]:
        with closing(self._connect()) as connection:
            return connection.execute(
                """
                SELECT ticket, symbol, opened_at, updated_at,
                       side, open_price, stop_loss, take_profit, profit, bid, ask
                FROM current_trades
                ORDER BY opened_at, ticket
                LIMIT 1
                """
            ).fetchone()

    def fetch_recent_events(self, limit: int = 50) -> list[sqlite3.Row]:
        with closing(self._connect()) as connection:
            return connection.execute(
                """
                SELECT event_time, cycle_id, ticket, event_type,
                       side, open_price, stop_loss, take_profit, profit, bid, ask, changed_fields
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
                event_time, cycle_id, ticket, event_type, symbol, side,
                open_price, stop_loss, take_profit, profit, bid, ask,
                changed_fields, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_time,
                cycle_id,
                ticket,
                event_type,
                trade["symbol"],
                trade["side"],
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
                ticket, symbol, side, opened_at, updated_at,
                open_price, stop_loss, take_profit, profit, bid, ask, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticket) DO UPDATE SET
                symbol = excluded.symbol,
                side = excluded.side,
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
                trade["side"],
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
