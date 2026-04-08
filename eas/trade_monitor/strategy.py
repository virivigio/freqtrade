import random


TRADE_COMMAND_PROBABILITY = 1 / 80
DEFAULT_COMMAND_LOT = 0.01


def decide_trade_command(trades: list[dict]) -> dict:
    if random.random() >= TRADE_COMMAND_PROBABILITY:
        return {"action": "NONE"}
    if trades:
        return {
            "action": "CLOSE",
            "reason": "demo_random_close_when_trade_exists",
        }
    side = random.choice(["BUY", "SELL"])
    return {
        "action": "OPEN",
        "side": side,
        "lot": DEFAULT_COMMAND_LOT,
        "reason": "demo_random_open_when_no_trade_exists",
    }
