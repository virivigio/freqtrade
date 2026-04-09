from trade_monitor.strategies.base import StrategyContext


DEFAULT_COMMAND_LOT = 0.01
IMPULSE_THRESHOLD = 7.0
TRIGGER_RECOVERY = 2.0
TAKE_PROFIT_DISTANCE = 3.0
STOP_LOSS_DISTANCE = 5.0
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


def is_long_stabilization(stabilization, impulse_last) -> bool:
    return (
        candle_range(stabilization) <= STABILIZATION_MAX_RANGE
        and candle_body(stabilization) <= STABILIZATION_MAX_BODY
        and float(stabilization["low"]) >= float(impulse_last["close"]) - STABILIZATION_MAX_EXTENSION
    )


def is_short_stabilization(stabilization, impulse_last) -> bool:
    return (
        candle_range(stabilization) <= STABILIZATION_MAX_RANGE
        and candle_body(stabilization) <= STABILIZATION_MAX_BODY
        and float(stabilization["high"]) <= float(impulse_last["close"]) + STABILIZATION_MAX_EXTENSION
    )


def try_long_setup(closed_candles: list, live_price: float) -> dict | None:
    if len(closed_candles) < 2:
        return None

    stabilization = closed_candles[-1]
    # Scan the most recent 1-3 closed candles before stabilization as the impulse leg.
    for impulse_size in range(1, 4):
        if len(closed_candles) < impulse_size + 1:
            continue
        impulse = closed_candles[-(impulse_size + 1) : -1]
        if any(float(candle["close"]) >= float(candle["open"]) for candle in impulse):
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
    return None


def try_short_setup(closed_candles: list, live_price: float) -> dict | None:
    if len(closed_candles) < 2:
        return None

    stabilization = closed_candles[-1]
    # Symmetric scan for an upward impulse in the most recent 1-3 closed candles.
    for impulse_size in range(1, 4):
        if len(closed_candles) < impulse_size + 1:
            continue
        impulse = closed_candles[-(impulse_size + 1) : -1]
        if any(float(candle["close"]) <= float(candle["open"]) for candle in impulse):
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
    return None


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

    signal = try_long_setup(context.closed_candles, live_price)
    if signal is not None:
        return signal

    # If no long reversal is active, evaluate the symmetric short setup on the same window.
    signal = try_short_setup(context.closed_candles, live_price)
    if signal is not None:
        return signal

    return {
        "command": {"action": "NONE"},
        "insight": {
            "phase": "idle",
            "has_current_trade": False,
            "live_price": round(live_price, 2),
            "reason": "no_valid_setup",
        },
    }
