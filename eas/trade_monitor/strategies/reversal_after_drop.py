from trade_monitor.strategies.base import StrategyContext


DEFAULT_COMMAND_LOT = 0.01
IMPULSE_THRESHOLD = 5.0
TRIGGER_RECOVERY = 1.0
TAKE_PROFIT_DISTANCE = 3.0
STOP_LOSS_DISTANCE = 4.0
STABILIZATION_MAX_RANGE = 3.0
STABILIZATION_MAX_BODY = 1.5
STABILIZATION_MAX_EXTENSION = 1.0


def candle_body(candle) -> float:
    return abs(float(candle["close"]) - float(candle["open"]))


def candle_range(candle) -> float:
    return float(candle["high"]) - float(candle["low"])


def current_price(context: StrategyContext) -> float | None:
    if not context.current_candle_states:
        return None
    return float(context.current_candle_states[-1]["close"])


def command_is_open(result: dict | None) -> bool:
    return result is not None and result.get("command", {}).get("action") == "OPEN"


def insight_priority(result: dict | None) -> int:
    if result is None:
        return -1
    phase = result.get("insight", {}).get("phase", "idle")
    priorities = {
        "signal_ready": 3,
        "waiting_trigger": 2,
        "waiting_stabilization": 1,
        "idle": 0,
    }
    return priorities.get(phase, 0)


def insight_impulse_value(result: dict | None) -> float:
    if result is None:
        return -1.0
    return float(result.get("insight", {}).get("impulse_value", -1.0))


def is_long_stabilization(stabilization, impulse_last) -> bool:
    # Treat stabilization as a small pause candle: limited range, small body,
    # and no meaningful breakdown below where the impulse leg had just stopped.
    return (
        candle_range(stabilization) <= STABILIZATION_MAX_RANGE
        and candle_body(stabilization) <= STABILIZATION_MAX_BODY
        and float(stabilization["low"]) >= float(impulse_last["close"]) - STABILIZATION_MAX_EXTENSION
    )


def is_short_stabilization(stabilization, impulse_last) -> bool:
    # Symmetric pause after an upward impulse: limited range, small body,
    # and no meaningful breakout above where the impulse leg had just stopped.
    return (
        candle_range(stabilization) <= STABILIZATION_MAX_RANGE
        and candle_body(stabilization) <= STABILIZATION_MAX_BODY
        and float(stabilization["high"]) <= float(impulse_last["close"]) + STABILIZATION_MAX_EXTENSION
    )


def try_long_setup(closed_candles: list, live_price: float) -> dict | None:
    if len(closed_candles) < 2:
        return None

    # Long setup = sharp drop in the last 1-3 closed candles, then one pause candle,
    # then a live recovery strong enough to confirm the reversal attempt.
    stabilization = closed_candles[-1]
    last_rejection = None
    # Scan the most recent 1-3 closed candles before stabilization as the impulse leg.
    for impulse_size in range(1, 6):
        if len(closed_candles) < impulse_size + 1:
            continue
        impulse = closed_candles[-(impulse_size + 1) : -1]
        if any(float(candle["close"]) >= float(candle["open"]) for candle in impulse):
            last_rejection = {
                "command": {"action": "NONE"},
                "insight": {
                    "phase": "idle",
                    "direction": "BUY",
                    "impulse_candles": impulse_size,
                    "reason": "impulse_not_all_bearish",
                },
            }
            continue

        first_open = float(impulse[0]["open"])
        last_close = float(impulse[-1]["close"])
        drop = first_open - last_close
        trigger_price = last_close + TRIGGER_RECOVERY
        stabilization_ok = is_long_stabilization(stabilization, impulse[-1])
        insight = {
            "phase": "waiting_stabilization" if not stabilization_ok else "waiting_trigger",
            "direction": "BUY",
            "impulse_candles": impulse_size,
            "impulse_value": round(drop, 2),
            "stabilization_ok": stabilization_ok,
            "trigger_reference": round(last_close, 2),
            "trigger_price": round(trigger_price, 2),
            "live_recovery": round(live_price - last_close, 2),
            "recovery_needed": round(TRIGGER_RECOVERY, 2),
        }
        if drop < IMPULSE_THRESHOLD:
            last_rejection = {
                "command": {"action": "NONE"},
                "insight": {
                    **insight,
                    "phase": "idle",
                    "reason": "impulse_below_threshold",
                },
            }
            continue

        # After the drop, require one closed candle that looks like a pause rather than a fresh breakdown.
        if not stabilization_ok:
            return {"command": {"action": "NONE"}, "insight": insight}

        # Entry only triggers once the live candle recovers enough from the close before stabilization.
        if live_price < trigger_price:
            return {"command": {"action": "NONE"}, "insight": insight}

        insight["phase"] = "signal_ready"
        return {
            "command": {
                "action": "OPEN",
                "side": "BUY",
                "lot": DEFAULT_COMMAND_LOT,
                "stop_loss": live_price - STOP_LOSS_DISTANCE,
                "take_profit": live_price + TAKE_PROFIT_DISTANCE,
                "reason": f"reversal_after_drop_long_{impulse_size}c",
            },
            "insight": insight,
        }
    return last_rejection


def try_short_setup(closed_candles: list, live_price: float) -> dict | None:
    if len(closed_candles) < 2:
        return None

    stabilization = closed_candles[-1]
    last_rejection = None
    # Symmetric scan for an upward impulse in the most recent 1-3 closed candles.
    for impulse_size in range(1, 6):
        if len(closed_candles) < impulse_size + 1:
            continue
        impulse = closed_candles[-(impulse_size + 1) : -1]
        if any(float(candle["close"]) <= float(candle["open"]) for candle in impulse):
            last_rejection = {
                "command": {"action": "NONE"},
                "insight": {
                    "phase": "idle",
                    "direction": "SELL",
                    "impulse_candles": impulse_size,
                    "reason": "impulse_not_all_bullish",
                },
            }
            continue

        first_open = float(impulse[0]["open"])
        last_close = float(impulse[-1]["close"])
        rise = last_close - first_open
        trigger_price = last_close - TRIGGER_RECOVERY
        stabilization_ok = is_short_stabilization(stabilization, impulse[-1])
        insight = {
            "phase": "waiting_stabilization" if not stabilization_ok else "waiting_trigger",
            "direction": "SELL",
            "impulse_candles": impulse_size,
            "impulse_value": round(rise, 2),
            "stabilization_ok": stabilization_ok,
            "trigger_reference": round(last_close, 2),
            "trigger_price": round(trigger_price, 2),
            "live_recovery": round(last_close - live_price, 2),
            "recovery_needed": round(TRIGGER_RECOVERY, 2),
        }
        if rise < IMPULSE_THRESHOLD:
            last_rejection = {
                "command": {"action": "NONE"},
                "insight": {
                    **insight,
                    "phase": "idle",
                    "reason": "impulse_below_threshold",
                },
            }
            continue

        # Require a pause after the rally before allowing the reversal trigger.
        if not stabilization_ok:
            return {"command": {"action": "NONE"}, "insight": insight}

        # The short trigger waits for the live candle to give back enough of the last impulse close.
        if live_price > trigger_price:
            return {"command": {"action": "NONE"}, "insight": insight}

        insight["phase"] = "signal_ready"
        return {
            "command": {
                "action": "OPEN",
                "side": "SELL",
                "lot": DEFAULT_COMMAND_LOT,
                "stop_loss": live_price + STOP_LOSS_DISTANCE,
                "take_profit": live_price - TAKE_PROFIT_DISTANCE,
                "reason": f"reversal_after_drop_short_{impulse_size}c",
            },
            "insight": insight,
        }
    return last_rejection


def decide_trade_command(context: StrategyContext) -> dict:
    # Strategy is single-position: if a trade is already open, only expose insight.
    if context.current_trade is not None:
        return {
            "command": {"action": "NONE"},
            "insight": {
                "phase": "in_trade",
                "has_current_trade": True,
            },
        }

    live_price = current_price(context)
    if live_price is None:
        return {
            "command": {"action": "NONE"},
            "insight": {
                "phase": "idle",
                "has_current_trade": False,
                "reason": "no_current_price",
            },
        }

    long_result = try_long_setup(context.closed_candles, live_price)
    short_result = try_short_setup(context.closed_candles, live_price)

    if command_is_open(long_result):
        return long_result
    if command_is_open(short_result):
        return short_result

    if insight_priority(short_result) > insight_priority(long_result):
        return short_result
    if insight_priority(long_result) > insight_priority(short_result):
        return long_result
    if insight_impulse_value(short_result) > insight_impulse_value(long_result):
        return short_result

    if long_result is not None:
        return long_result
    if short_result is not None:
        return short_result

    return {
        "command": {"action": "NONE"},
        "insight": {
            "phase": "idle",
            "has_current_trade": False,
            "live_price": round(live_price, 2),
            "reason": "no_valid_setup",
        },
    }
