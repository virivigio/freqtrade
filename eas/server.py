#!/usr/bin/env python3
import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


HOST = "0.0.0.0"
PORT = 80
DB_PATH = Path(__file__).with_name("trade_log.sqlite3")
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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def parse_payload(body: bytes) -> list[dict]:
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
    return trades


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
            f"<td>{escape(row['updated_at'])}</td>"
            "</tr>"
        )

    event_rows = []
    for row in recent_events:
        event_rows.append(
            "<tr>"
            f"<td>{escape(row['event_time'])}</td>"
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
        "<table><thead><tr><th>Ora UTC</th><th>Evento</th><th>Ticket</th>"
        "<th>SL</th><th>TP</th><th>Profit</th><th>Bid</th><th>Ask</th></tr></thead><tbody>"
        + ("".join(event_rows) if event_rows else "<tr><td colspan='8'>Nessun evento registrato.</td></tr>")
        + "</tbody></table>"
    )

    if last_api_call:
        last_call_text = escape(last_api_call["received_at"])
    else:
        last_call_text = "Nessuna chiamata ricevuta"

    if recent_errors:
        error_items = []
        for row in recent_errors:
            error_items.append(
                "<details class='error-item'>"
                f"<summary>{escape(row['created_at'])} | {escape(row['error_message'])}</summary>"
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
        <h2>Errori API</h2>
        {errors_html}
      </section>
    </div>
  </main>
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
        if parsed.path != "/":
            self._json_response(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        html = render_homepage(self.get_store()).encode("utf-8")
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
            payload = json.loads(payload_text)
            trades = parse_payload(body)
            result = store.ingest_trade_list(trades)
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
