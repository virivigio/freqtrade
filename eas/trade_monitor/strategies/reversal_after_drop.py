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
    for impulse_size in range(1, 4):
        if len(closed_candles) < impulse_size + 1:
            continue
        impulse = closed_candles[-(impulse_size + 1) : -1]
        if any(float(candle["close"]) >= float(candle["open"]) for candle in impulse):
            continue

        first_open = float(impulse[0]["open"])
        last_close = float(impulse[-1]["close"])
        drop = first_open - last_close
        if drop < IMPULSE_THRESHOLD:
            continue

        if not is_long_stabilization(stabilization, impulse[-1]):
            continue

        trigger_price = last_close + TRIGGER_RECOVERY
        if live_price < trigger_price:
            continue

        return {
            "action": "OPEN",
            "side": "BUY",
            "lot": DEFAULT_COMMAND_LOT,
            "stop_loss": live_price - STOP_LOSS_DISTANCE,
            "take_profit": live_price + TAKE_PROFIT_DISTANCE,
            "reason": f"reversal_after_drop_long_{impulse_size}c",
        }
    return None


def try_short_setup(closed_candles: list, live_price: float) -> dict | None:
    if len(closed_candles) < 2:
        return None

    stabilization = closed_candles[-1]
    for impulse_size in range(1, 4):
        if len(closed_candles) < impulse_size + 1:
            continue
        impulse = closed_candles[-(impulse_size + 1) : -1]
        if any(float(candle["close"]) <= float(candle["open"]) for candle in impulse):
            continue

        first_open = float(impulse[0]["open"])
        last_close = float(impulse[-1]["close"])
        rise = last_close - first_open
        if rise < IMPULSE_THRESHOLD:
            continue

        if not is_short_stabilization(stabilization, impulse[-1]):
            continue

        trigger_price = last_close - TRIGGER_RECOVERY
        if live_price > trigger_price:
            continue

        return {
            "action": "OPEN",
            "side": "SELL",
            "lot": DEFAULT_COMMAND_LOT,
            "stop_loss": live_price + STOP_LOSS_DISTANCE,
            "take_profit": live_price - TAKE_PROFIT_DISTANCE,
            "reason": f"reversal_after_drop_short_{impulse_size}c",
        }
    return None


def decide_trade_command(context: StrategyContext) -> dict:
    if context.current_trade is not None:
        return {"action": "NONE"}

    live_price = current_price(context)
    if live_price is None:
        return {"action": "NONE"}

    signal = try_long_setup(context.closed_candles, live_price)
    if signal is not None:
        return signal

    signal = try_short_setup(context.closed_candles, live_price)
    if signal is not None:
        return signal

    return {"action": "NONE"}

