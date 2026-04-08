import json
from datetime import datetime, timezone
from html import escape
from math import floor
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo


HOST = "0.0.0.0"
PORT = 80
DB_PATH = Path(__file__).resolve().parent.parent / "trade_log.sqlite3"
ITALY_TZ = ZoneInfo("Europe/Rome")
PAYLOAD_FIELDS = (
    "side",
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


def normalize_trade(raw_trade: dict) -> dict:
    if not isinstance(raw_trade, dict):
        raise ValueError("Each trade must be an object.")

    symbol = str(raw_trade.get("symbol", ""))

    try:
        ticket = int(raw_trade["ticket"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Trade ticket is required and must be an integer.") from exc

    try:
        side = str(raw_trade["side"]).upper()
    except KeyError as exc:
        raise ValueError("Trade field 'side' is required.") from exc

    if side not in ("BUY", "SELL"):
        raise ValueError("Trade field 'side' must be BUY or SELL.")

    normalized = {"ticket": ticket, "symbol": symbol, "side": side}
    for field in PAYLOAD_FIELDS:
        if field == "side":
            continue
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


def format_price(value: float) -> str:
    return f"{float(value):.2f}"


def render_hero_info(
    last_api_call: Optional[dict],
    latest_error: Optional[dict],
    commands_enabled: bool,
) -> str:
    if last_api_call:
        last_call_text = escape(format_timestamp_for_header(last_api_call["received_at"]))
    else:
        last_call_text = "Nessuna chiamata ricevuta"

    html = [f"<p class='meta'>Ultima Chiamata {last_call_text}</p>"]
    status_label = "Abilitati" if commands_enabled else "Disabilitati"
    button_label = "Disabilita Apertura Trade" if commands_enabled else "Abilita Apertura Trade"
    button_class = "control-btn is-on" if commands_enabled else "control-btn is-off"
    html.append(
        "<div class='control-row'>"
        f"<p class='meta'>Comandi MT4: <strong>{status_label}</strong></p>"
        f"<button type='button' class='{button_class}' onclick='toggleTradingCommands()'>{button_label}</button>"
        "</div>"
    )
    if latest_error is not None:
        html.append(
            "<p class='meta error-inline'>"
            f"Ultimo errore API {escape(format_timestamp_for_table(latest_error['created_at']))}: "
            f"{escape(latest_error['error_message'])}"
            "</p>"
        )
    return "".join(html)
