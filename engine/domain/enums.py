"""Enumerations shared by the engine. String-valued so they persist cleanly."""
from enum import Enum


class TradingMode(str, Enum):
    PAPER = "PAPER"
    TESTNET = "TESTNET"
    LIVE = "LIVE"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS_LIMIT = "STOP_LOSS_LIMIT"


class OrderPurpose(str, Enum):
    ENTRY = "ENTRY"                  # strategy BUY
    EXIT = "EXIT"                    # strategy SELL
    STOP_EXIT = "STOP_EXIT"          # software stop-loss market exit
    PROTECTIVE_STOP = "PROTECTIVE_STOP"  # exchange-side resting stop order


class OrderStatus(str, Enum):
    INTENT_CREATED = "INTENT_CREATED"
    SUBMITTING = "SUBMITTING"
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_STATUSES


TERMINAL_STATUSES = frozenset({OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED})


class ReconciliationStatus(str, Enum):
    CONNECTING = "CONNECTING"
    RECONCILING = "RECONCILING"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    HALTED = "HALTED"


class MismatchSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"   # any unresolved CRITICAL mismatch halts trading


class SubmitOutcomeKind(str, Enum):
    ACKNOWLEDGED = "ACKNOWLEDGED"   # exchange accepted; status/fills in report
    REJECTED = "REJECTED"           # exchange definitively refused
    AMBIGUOUS = "AMBIGUOUS"         # timeout / network / 5xx / -1007: state unknown
