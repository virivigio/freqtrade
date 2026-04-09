import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


def sample_payload() -> dict:
    return {
        "trades": [
            {
                "ticket": 2001,
                "symbol": "XAUUSD.s",
                "side": "BUY",
                "open_price": 2320.10,
                "stop_loss": 2310.00,
                "take_profit": 2340.00,
                "profit": 14.35,
                "bid": 2321.50,
                "ask": 2321.70,
            }
        ],
        "candles": [
            {
                "symbol": "XAUUSD.s",
                "timeframe": "M1",
                "open_time": 1712610000,
                "open": 2320.10,
                "high": 2321.50,
                "low": 2319.90,
                "close": 2321.20,
                "volume": 87,
                "is_closed": True,
            },
            {
                "symbol": "XAUUSD.s",
                "timeframe": "M1",
                "open_time": 1712610060,
                "open": 2321.20,
                "high": 2321.70,
                "low": 2321.00,
                "close": 2321.40,
                "volume": 12,
                "is_closed": False,
            },
        ],
    }


class DummyHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class HandlerHarness(server.TradeRequestHandler):
    def __init__(self):
        pass


class HttpEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "http-test.sqlite3"
        self.store = server.TradeStore(self.db_path)
        server.TradeRequestHandler.store = self.store

    def tearDown(self) -> None:
        server.TradeRequestHandler.store = None
        self.temp_dir.cleanup()

    def build_handler(self, *, path: str, method: str, body: bytes = b"", headers: dict | None = None):
        handler = HandlerHarness.__new__(HandlerHarness)
        handler.path = path
        handler.command = method
        handler.request_version = "HTTP/1.1"
        handler.headers = DummyHeaders(headers or {})
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.client_address = ("127.0.0.1", 12345)
        handler.server = None
        status_holder = {"status": None, "headers": []}

        def send_response(code, message=None):
            status_holder["status"] = code

        def send_header(name, value):
            status_holder["headers"].append((name, value))

        def end_headers():
            return

        handler.send_response = send_response
        handler.send_header = send_header
        handler.end_headers = end_headers
        return handler, status_holder

    def read_json_body(self, handler) -> dict:
        return json.loads(handler.wfile.getvalue().decode("utf-8"))

    def read_text_body(self, handler) -> str:
        return handler.wfile.getvalue().decode("utf-8")

    def test_get_homepage_returns_html(self) -> None:
        handler, status = self.build_handler(path="/", method="GET")
        handler.do_GET()

        self.assertEqual(status["status"], 200)
        body = self.read_text_body(handler)
        self.assertIn("MT4 Trade Monitor", body)
        self.assertIn("Trade in Corso", body)

    def test_get_dashboard_returns_fragments_json(self) -> None:
        self.store.ingest_trade_list(sample_payload()["trades"])
        self.store.ingest_candles(sample_payload()["candles"])

        handler, status = self.build_handler(path="/api/dashboard", method="GET")
        handler.do_GET()
        payload = self.read_json_body(handler)

        self.assertEqual(status["status"], 200)
        self.assertIn("hero_info_html", payload)
        self.assertIn("trade_table_html", payload)
        self.assertIn("recent_trades_html", payload)
        self.assertIn("price_chart_html", payload)
        self.assertIn("candle_chart_html", payload)

    def test_post_api_trades_returns_command_and_persists(self) -> None:
        body = json.dumps(sample_payload()).encode("utf-8")
        handler, status = self.build_handler(
            path="/api/trades",
            method="POST",
            body=body,
            headers={"Content-Length": str(len(body))},
        )

        with patch("server.decide_trade_command", return_value={"action": "OPEN", "side": "BUY", "lot": 0.01}):
            handler.do_POST()

        payload = self.read_json_body(handler)
        self.assertEqual(status["status"], 200)
        self.assertIn("trades", payload)
        self.assertIn("candles", payload)
        self.assertIn("command", payload)
        self.assertEqual(payload["command"]["action"], "NONE")
        self.assertEqual(len(self.store.fetch_current_trades()), 1)
        self.assertEqual(len(self.store.fetch_recent_closed_candles()), 1)

    def test_post_api_trades_rejects_invalid_payload(self) -> None:
        body = b'{"trades":"bad"}'
        handler, status = self.build_handler(
            path="/api/trades",
            method="POST",
            body=body,
            headers={"Content-Length": str(len(body))},
        )
        handler.do_POST()

        self.assertEqual(status["status"], 400)
        payload = self.read_json_body(handler)
        self.assertIn("error", payload)

    def test_post_toggle_commands_flips_state_and_enables_command_generation(self) -> None:
        toggle_handler, toggle_status = self.build_handler(
            path="/api/commands/toggle",
            method="POST",
            body=b"",
            headers={"Content-Length": "0"},
        )
        toggle_handler.do_POST()
        toggle_payload = self.read_json_body(toggle_handler)

        self.assertEqual(toggle_status["status"], 200)
        self.assertTrue(toggle_payload["commands_enabled"])
        self.assertTrue(self.store.get_commands_enabled())

        body = json.dumps(sample_payload()).encode("utf-8")
        handler, status = self.build_handler(
            path="/api/trades",
            method="POST",
            body=body,
            headers={"Content-Length": str(len(body))},
        )
        with patch("server.decide_trade_command", return_value={"action": "CLOSE"}):
            handler.do_POST()

        payload = self.read_json_body(handler)
        self.assertEqual(status["status"], 200)
        self.assertEqual(payload["command"]["action"], "CLOSE")


if __name__ == "__main__":
    unittest.main()
