"""Centralized risk controls. Strategy code must never place orders directly."""
from dataclasses import dataclass


@dataclass
class RiskState:
    equity: float
    day_start_equity: float
    halted: bool = False


def position_size(equity: float, price: float, risk_pct: float, stop_loss_pct: float) -> float:
    if equity <= 0 or price <= 0 or risk_pct <= 0 or stop_loss_pct <= 0:
        return 0.0
    risk_amount = equity * (risk_pct / 100)
    position_value = min(risk_amount / (stop_loss_pct / 100), equity)
    return max(0.0, position_value / price)


def stop_loss_price(entry_price: float, side: str, stop_loss_pct: float) -> float:
    if side == "long":
        return entry_price * (1 - stop_loss_pct / 100)
    return entry_price * (1 + stop_loss_pct / 100)


def check_daily_loss_limit(state: RiskState, limit_pct: float) -> bool:
    if state.day_start_equity <= 0:
        return state.halted
    drawdown_pct = (state.day_start_equity - state.equity) / state.day_start_equity * 100
    if drawdown_pct >= limit_pct:
        state.halted = True
    return state.halted


def reset_day(state: RiskState):
    state.day_start_equity = state.equity
    state.halted = False
