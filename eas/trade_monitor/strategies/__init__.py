from trade_monitor.strategies.base import StrategyContext
from trade_monitor.strategies.random_demo import decide_trade_command as decide_random_demo_trade_command
from trade_monitor.strategies.reversal_after_drop import DEFAULT_COMMAND_LOT
from trade_monitor.strategies.reversal_after_drop import decide_trade_command as decide_reversal_after_drop_command


ACTIVE_STRATEGY = "reversal_after_drop"


def decide_trade_command(context: StrategyContext) -> dict:
    if ACTIVE_STRATEGY == "reversal_after_drop":
        return decide_reversal_after_drop_command(context)
    if ACTIVE_STRATEGY == "random_demo":
        return decide_random_demo_trade_command(context)
    raise ValueError(f"Unsupported active strategy '{ACTIVE_STRATEGY}'.")
