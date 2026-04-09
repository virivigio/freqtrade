import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import server
from trade_monitor.strategies import DEFAULT_COMMAND_LOT, decide_trade_command
from trade_monitor.strategies.base import StrategyContext


def sample_trade(
    ticket: int,
    *,
    symbol: str = "XAUUSD.s",
    side: str = "BUY",
    open_price: float = 2320.10,
    stop_loss: float = 2310.00,
    take_profit: float = 2340.00,
    profit: float = 14.35,
    bid: float = 2321.50,
    ask: float = 2321.70,
) -> dict:
    return {
        "ticket": ticket,
        "symbol": symbol,
        "side": side,
        "open_price": open_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "profit": profit,
        "bid": bid,
        "ask": ask,
    }


def sample_candle(
    open_time: int,
    *,
    is_closed: bool,
    symbol: str = "XAUUSD.s",
    timeframe: str = "M1",
    open: float = 2320.10,
    high: float = 2321.50,
    low: float = 2319.90,
    close: float = 2321.20,
    volume: int = 87,
) -> dict:
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "open_time": open_time,
        "open": open,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "is_closed": is_closed,
    }


class TradeStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.sqlite3"
        self.store = server.TradeStore(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_ingest_trade_list_tracks_open_update_close(self) -> None:
        first = self.store.ingest_trade_list([sample_trade(1001)])
        self.assertEqual(first["inserted"], 1)
        self.assertEqual(first["updated"], 0)
        self.assertEqual(first["closed"], 0)

        second = self.store.ingest_trade_list(
            [
                sample_trade(
                    1001,
                    stop_loss=2312.0,
                    take_profit=2342.0,
                    profit=20.0,
                    bid=2322.0,
                    ask=2322.2,
                )
            ]
        )
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["updated"], 1)
        self.assertEqual(second["closed"], 0)

        third = self.store.ingest_trade_list([])
        self.assertEqual(third["inserted"], 0)
        self.assertEqual(third["updated"], 0)
        self.assertEqual(third["closed"], 1)

        events = self.store.fetch_recent_events(limit=10)
        self.assertEqual([row["event_type"] for row in reversed(events)], ["OPEN", "UPDATE", "CLOSE"])

    def test_ingest_trade_list_ignores_profit_only_changes_for_event_log(self) -> None:
        self.store.ingest_trade_list([sample_trade(1002, profit=10.0)])
        result = self.store.ingest_trade_list([sample_trade(1002, profit=25.0, bid=2325.0, ask=2325.2)])
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["unchanged"], 1)

        events = self.store.fetch_recent_events(limit=10)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "OPEN")

        current_trade = self.store.fetch_current_trades()[0]
        self.assertEqual(float(current_trade["profit"]), 25.0)
        self.assertEqual(float(current_trade["bid"]), 2325.0)

    def test_ingest_candles_inserts_closed_ignores_duplicates_and_rotates_current_states(self) -> None:
        result_first = self.store.ingest_candles(
            [
                sample_candle(1712610000, is_closed=True, close=2320.0),
                sample_candle(1712610060, is_closed=True, close=2321.0),
                sample_candle(1712610120, is_closed=False, close=2321.5, volume=12),
            ]
        )
        self.assertEqual(result_first["inserted_closed_candles"], 2)
        self.assertEqual(result_first["inserted_current_candle_states"], 1)

        result_second = self.store.ingest_candles(
            [
                sample_candle(1712610000, is_closed=True, close=2320.0),
                sample_candle(1712610060, is_closed=True, close=2321.0),
                sample_candle(1712610120, is_closed=False, close=2321.7, volume=18),
            ]
        )
        self.assertEqual(result_second["inserted_closed_candles"], 0)
        self.assertEqual(result_second["inserted_current_candle_states"], 1)

        result_third = self.store.ingest_candles(
            [
                sample_candle(1712610060, is_closed=True, close=2321.0),
                sample_candle(1712610120, is_closed=True, close=2321.9),
                sample_candle(1712610180, is_closed=False, close=2322.2, volume=5),
            ]
        )
        self.assertEqual(result_third["inserted_closed_candles"], 1)
        self.assertEqual(result_third["inserted_current_candle_states"], 1)

        with self.store._connect() as connection:
            closed_count = connection.execute("SELECT COUNT(*) FROM closed_candles").fetchone()[0]
            current_rows = connection.execute(
                "SELECT candle_open_time, close FROM current_candle_states ORDER BY id"
            ).fetchall()

        self.assertEqual(closed_count, 3)
        self.assertEqual(len(current_rows), 1)
        self.assertEqual(current_rows[0][0], 1712610180)
        self.assertEqual(float(current_rows[0][1]), 2322.2)

    def test_commands_enabled_defaults_to_disabled_and_can_toggle(self) -> None:
        self.assertFalse(self.store.get_commands_enabled())
        self.assertTrue(self.store.set_commands_enabled(True))
        self.assertTrue(self.store.get_commands_enabled())
        self.assertFalse(self.store.set_commands_enabled(False))
        self.assertFalse(self.store.get_commands_enabled())


class ServerFunctionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.sqlite3"
        self.store = server.TradeStore(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def build_strategy_context(self, current_trade=None) -> StrategyContext:
        return StrategyContext(
            closed_candles=[],
            current_candle_states=[],
            current_trade=current_trade,
        )

    def test_decide_trade_command_returns_none_without_setup(self) -> None:
        command = decide_trade_command(self.build_strategy_context())
        self.assertEqual(command, {"action": "NONE"})

    def test_decide_trade_command_opens_long_after_drop_setup(self) -> None:
        context = StrategyContext(
            closed_candles=[
                sample_candle(1712610000, is_closed=True, open=4720.0, high=4720.0, low=4716.0, close=4716.0),
                sample_candle(1712610060, is_closed=True, open=4716.0, high=4716.0, low=4711.0, close=4711.0),
                sample_candle(1712610120, is_closed=True, open=4711.2, high=4712.0, low=4710.8, close=4711.4),
            ],
            current_candle_states=[
                {
                    "close": 4713.2,
                }
            ],
            current_trade=None,
        )

        command = decide_trade_command(context)

        self.assertEqual(command["action"], "OPEN")
        self.assertEqual(command["side"], "BUY")
        self.assertEqual(command["lot"], DEFAULT_COMMAND_LOT)
        self.assertEqual(command["stop_loss"], 4708.2)
        self.assertEqual(command["take_profit"], 4716.2)

    def test_decide_trade_command_returns_none_when_trade_already_exists(self) -> None:
        context = StrategyContext(
            closed_candles=[
                sample_candle(1712610000, is_closed=True, open=4720.0, high=4720.0, low=4716.0, close=4716.0),
                sample_candle(1712610060, is_closed=True, open=4716.0, high=4716.0, low=4711.0, close=4711.0),
                sample_candle(1712610120, is_closed=True, open=4711.2, high=4712.0, low=4710.8, close=4711.4),
            ],
            current_candle_states=[
                {
                    "close": 4713.2,
                }
            ],
            current_trade=sample_trade(1),
        )
        command = decide_trade_command(context)
        self.assertEqual(command, {"action": "NONE"})

    def test_render_dashboard_fragments_contains_live_sections(self) -> None:
        self.store.ingest_candles(
            [
                sample_candle(1712610000, is_closed=True, close=4720.0, high=4725.0, low=4718.0),
                sample_candle(1712610060, is_closed=False, close=4721.0, high=4722.0, low=4719.0),
            ]
        )
        self.store.record_api_call("/api/trades", "127.0.0.1", {"trades": []}, {"ok": True})
        self.store.record_api_error("/api/trades", "127.0.0.1", '{"test":true}', "Errore demo")
        self.store.set_commands_enabled(True)
        marker_time = datetime.fromtimestamp(1712610000, tz=timezone.utc).isoformat()
        with self.store._connect() as connection:
            connection.execute(
                """
                INSERT INTO trade_events (
                    event_time, cycle_id, ticket, event_type, symbol, side,
                    open_price, stop_loss, take_profit, profit, bid, ask,
                    changed_fields, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    marker_time,
                    "cycle-marker",
                    1003,
                    "OPEN",
                    "XAUUSD.s",
                    "BUY",
                    4721.0,
                    4710.0,
                    4730.0,
                    0.0,
                    4721.0,
                    4721.2,
                    "{}",
                    "{}",
                ),
            )
            connection.commit()

        fragments = server.render_dashboard_fragments(self.store)

        self.assertIn("Ultima Chiamata", fragments["hero_info_html"])
        self.assertIn("Ultimo errore API", fragments["hero_info_html"])
        self.assertIn("Disabilita Apertura Trade", fragments["hero_info_html"])
        self.assertIn("Side", fragments["trade_table_html"])
        self.assertIn("Ticket", fragments["trade_table_html"])
        self.assertIn("Ultimo prezzo", fragments["price_chart_html"])
        self.assertIn("Finestra: <strong>60 minuti</strong>", fragments["candle_chart_html"])
        self.assertIn("<title>OPEN BUY", fragments["candle_chart_html"])

    def test_render_recent_trades_table_shows_only_close_events(self) -> None:
        self.store.ingest_trade_list([sample_trade(1004)])
        self.store.ingest_trade_list([sample_trade(1004, stop_loss=2315.0)])
        self.store.ingest_trade_list([])

        html = server.render_recent_trades_table(self.store.fetch_recent_events(limit=10))

        self.assertNotIn("Nessun trade chiuso", html)
        self.assertIn(">1004<", html)
        self.assertIn(">BUY<", html)
        self.assertNotIn("UPDATE", html)
        self.assertNotIn("OPEN", html)


if __name__ == "__main__":
    unittest.main()
