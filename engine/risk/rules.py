"""Pure, individually testable entry rules. Each returns (ok, reason)."""
from decimal import Decimal


def max_open_positions(open_count: int, limit: int) -> tuple[bool, str]:
    return (open_count < limit, f"open positions {open_count} >= MAX_OPEN_POSITIONS {limit}")


def not_daily_halted(halted: bool) -> tuple[bool, str]:
    return (not halted, "daily loss limit reached")


def min_notional(rules, qty: Decimal, price: Decimal) -> tuple[bool, str]:
    return (rules.is_tradeable(qty, price),
            f"qty {qty} @ {price} below exchange minimums (minQty {rules.min_qty}, minNotional {rules.min_notional})")
