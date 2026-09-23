"""AI layer for the live engine: a trained model (research/ml.py) on top of the unchanged V1 strategy.

Modes (STRATEGY_MODE):
  veto - V1 decides; the model may only BLOCK a V1 buy when p(up) < threshold. Exits are always V1's.
  ml   - the model decides: BUY when p >= threshold, SELL when p < exit_p.
The strategy still only returns a Signal: sizing, risk, stops, reconciliation and order handling are unchanged.

Safety: the model file is a pickle, which can execute code when loaded. It is loaded ONLY after its SHA-256
matches ML_MODEL_SHA256 from .env, and only if features and scikit-learn version match what it was trained with.
"""
from __future__ import annotations

import hashlib

import pandas as pd

import strategy as v1_strategy
from engine.strategy import ml_features as mf
from engine.strategy.strategy_service import MarketContext, MultiFactorStrategy


class ModelRejected(RuntimeError):
    pass


def load_verified(path: str, expected_sha256: str) -> dict:
    import joblib
    import sklearn
    with open(path, "rb") as f:
        actual = hashlib.sha256(f.read()).hexdigest()
    if actual != (expected_sha256 or "").lower():
        raise ModelRejected(f"model hash {actual[:12]}… does not match ML_MODEL_SHA256; refusing to load it")
    art = joblib.load(path)
    meta = art["meta"]
    if meta["features"] != mf.FEATURES or meta["feature_version"] != mf.FEATURE_VERSION:
        raise ModelRejected("model was trained on a different feature set")
    if meta["sklearn"] != sklearn.__version__:
        raise ModelRejected(f"model trained with scikit-learn {meta['sklearn']}, installed {sklearn.__version__}")
    return art


class AIStrategy:
    def __init__(self, cfg, artifact: dict | None = None):
        self.cfg = cfg
        self.v1 = MultiFactorStrategy(cfg)
        art = artifact or load_verified(cfg.ML_MODEL_PATH, cfg.ML_MODEL_SHA256)
        self.model, self.meta = art["model"], art["meta"]
        if self.meta["mode"] != cfg.STRATEGY_MODE:
            raise ModelRejected(f"model was validated for mode '{self.meta['mode']}', STRATEGY_MODE is '{cfg.STRATEGY_MODE}'")
        self.mode, self.threshold, self.exit_p = self.meta["mode"], float(self.meta["threshold"]), float(self.meta["exit_p"])
        self.name = f"{cfg.STRATEGY_NAME}+ai"
        self.version = f"{cfg.STRATEGY_VERSION}+{self.mode}-{(getattr(cfg, 'ML_MODEL_SHA256', '') or 'inline')[:8]}"

    def probability(self, candles: pd.DataFrame) -> float:
        feats = mf.window_features(candles, self.cfg)
        return float(self.model.predict_proba(pd.DataFrame([feats])[self.meta["features"]])[0, 1])

    def evaluate(self, ctx: MarketContext):
        base = self.v1.evaluate(ctx)
        p = self.probability(ctx.candles)
        note = f"AI p(24h up > {self.meta['edge'] * 100:.2f}%) = {p:.2f} (threshold {self.threshold:.2f})"
        if self.mode == "veto":
            action = base.action
            if action == "BUY" and p < self.threshold:
                action, note = "HOLD", note + " - V1 buy vetoed"
            return v1_strategy.Signal(action=action, score=base.score, reasons=[*base.reasons, note])
        action = "BUY" if p >= self.threshold else ("SELL" if p < self.exit_p else "HOLD")
        return v1_strategy.Signal(action=action, score=round(p, 4), reasons=[note, *base.reasons])


def build_strategy(cfg):
    mode = getattr(cfg, "STRATEGY_MODE", "v1")
    return MultiFactorStrategy(cfg) if mode == "v1" else AIStrategy(cfg)
