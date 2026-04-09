from dataclasses import dataclass
from typing import Optional

import sqlite3


@dataclass(frozen=True)
class StrategyContext:
    closed_candles: list[sqlite3.Row]
    current_candle_states: list[sqlite3.Row]
    current_trade: Optional[sqlite3.Row]

