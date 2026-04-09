#!/usr/bin/env python3
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse

from trade_monitor.core import DB_PATH, HOST, PORT, parse_payload
from trade_monitor.dashboard import render_dashboard_fragments, render_homepage, render_recent_trades_table
from trade_monitor.strategies import decide_trade_command
from trade_monitor.strategies.base import StrategyContext
from trade_monitor.store import TradeStore


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

        if parsed.path == "/api/commands/toggle":
            enabled = store.set_commands_enabled(not store.get_commands_enabled())
            self._json_response(HTTPStatus.OK, {"commands_enabled": enabled})
            return

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
            trade_result = store.ingest_trade_list(trades)
            candle_result = store.ingest_candles(candles)
            command = {"action": "NONE"}
            if store.get_commands_enabled():
                command = decide_trade_command(
                    StrategyContext(
                        closed_candles=store.fetch_recent_closed_candles(limit=60),
                        current_candle_states=store.fetch_recent_current_candle_states(limit=60),
                        current_trade=store.fetch_current_trade(),
                    )
                )
            result = {
                "trades": trade_result,
                "candles": candle_result,
                "command": command,
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
