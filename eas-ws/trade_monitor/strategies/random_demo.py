import random

from trade_monitor.strategies.base import StrategyContext


TRADE_COMMAND_PROBABILITY = 1 / 80
DEFAULT_COMMAND_LOT = 0.01


def decide_trade_command(context: StrategyContext) -> dict:
    if random.random() >= TRADE_COMMAND_PROBABILITY:
        return {
            "command": {"action": "NONE"},
            "insight": {
                "phase": "idle",
                "probability_gate_open": False,
                "has_current_trade": context.current_trade is not None,
            },
        }
    if context.current_trade is not None:
        return {
            "command": {
                "action": "CLOSE",
                "reason": "demo_random_close_when_trade_exists",
            },
            "insight": {
                "phase": "close_signal",
                "probability_gate_open": True,
                "has_current_trade": True,
            },
        }
    side = random.choice(["BUY", "SELL"])
    return {
        "command": {
            "action": "OPEN",
            "side": side,
            "lot": DEFAULT_COMMAND_LOT,
            "reason": "demo_random_open_when_no_trade_exists",
        },
        "insight": {
            "phase": "open_signal",
            "probability_gate_open": True,
            "has_current_trade": False,
            "direction": side,
        },
    }
