from trade_monitor.strategies.base import StrategyContext
from trade_monitor.strategies.random_demo import decide_trade_command as decide_random_demo_trade_command
from trade_monitor.strategies.reversal_after_drop import DEFAULT_COMMAND_LOT
from trade_monitor.strategies.reversal_after_drop import decide_trade_command as decide_reversal_after_drop_command


ACTIVE_STRATEGY = "reversal_after_drop"


def decide_trade_command(context: StrategyContext) -> dict:
    if ACTIVE_STRATEGY == "reversal_after_drop":
        result = decide_reversal_after_drop_command(context)
    elif ACTIVE_STRATEGY == "random_demo":
        result = decide_random_demo_trade_command(context)
    else:
        raise ValueError(f"Unsupported active strategy '{ACTIVE_STRATEGY}'.")
    insight = dict(result.get("insight", {}))
    insight.setdefault("strategy", ACTIVE_STRATEGY)
    result["insight"] = insight
    result.setdefault("command", {"action": "NONE"})
    return result
