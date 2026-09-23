"""RiskManager: wraps the unchanged V1 sizing / stop / daily-loss math
(risk_manager.py) and adds persisted daily state and exchange-rule rounding.

Documented V1.1 correctness fix: in V1 a daily-loss halt also skipped the
stop-loss check. Now the halt blocks NEW ENTRIES only; protective exits run.
"""
from __future__ import annotations

from decimal import Decimal

import risk_manager as v1
from engine.risk import rules as R


class RiskManager:
    def __init__(self, store, cfg):
        self.store = store
        self.cfg = cfg
        self.key = f"risk:{cfg.BOT_INSTANCE_ID}"

    def update_daily(self, equity: Decimal, today_iso: str) -> bool:
        st = self.store.kv_get(self.key) or {}
        if st.get("date") != today_iso:
            st = {"date": today_iso, "day_start_equity": str(equity), "halted": False}
        rs = v1.RiskState(equity=float(equity), day_start_equity=float(st["day_start_equity"]), halted=bool(st.get("halted")))
        st["halted"] = v1.check_daily_loss_limit(rs, self.cfg.DAILY_LOSS_LIMIT_PCT)
        self.store.kv_set(self.key, st)
        return st["halted"]

    def is_daily_halted(self) -> bool:
        return bool((self.store.kv_get(self.key) or {}).get("halted"))

    def size_entry(self, equity: Decimal, price: Decimal, rules, quote_free: Decimal | None) -> tuple[Decimal, str | None]:
        qty = Decimal(str(v1.position_size(float(equity), float(price), self.cfg.RISK_PER_TRADE_PCT, self.cfg.STOP_LOSS_PCT)))
        if quote_free is not None and price > 0:
            qty = min(qty, Decimal(quote_free) / Decimal(price))    # never size beyond spendable quote balance
        qty = rules.round_qty(qty)
        ok, reason = R.min_notional(rules, qty, Decimal(price))
        return (qty, None) if ok else (Decimal("0"), reason)

    def stop_price(self, entry_price: Decimal) -> Decimal:
        return Decimal(str(v1.stop_loss_price(float(entry_price), "long", self.cfg.STOP_LOSS_PCT)))

    def entry_checks(self, open_positions: int) -> list[str]:
        failures = []
        for ok, reason in (R.max_open_positions(open_positions, self.cfg.MAX_OPEN_POSITIONS),
                           R.not_daily_halted(self.is_daily_halted())):
            if not ok:
                failures.append(reason)
        return failures
