import risk_manager

def test_position_size():
    # risk $10 (1% of 1000) with a 2% stop -> $500 position -> 5 units at $100.
    # (The V1 test asserted 0.05, which contradicts the unchanged V1 formula.)
    qty=risk_manager.position_size(1000,100,1,2)
    assert round(qty,4)==5.0

def test_daily_loss():
    s=risk_manager.RiskState(950,1000)
    assert risk_manager.check_daily_loss_limit(s,5) is True
