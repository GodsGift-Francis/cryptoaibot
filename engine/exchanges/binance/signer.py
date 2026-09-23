"""Binance WebSocket API request signing (HMAC-SHA256 keys).

Per the current Spot WebSocket API docs ("SIGNED request example (HMAC)"):
take every param except `signature`, sort by name, join as `k=v` with `&`
(values UTF-8, not percent-encoded), HMAC-SHA256 with the secret, hex-encode.

REST signing is NOT done here: REST calls go through CCXT, which signs the
percent-encoded query string as required since the 2026-01-15 REST change.
"""
import hashlib
import hmac


def ws_signature_payload(params: dict) -> str:
    return "&".join(f"{k}={params[k]}" for k in sorted(params) if k != "signature")


def sign_ws_params(params: dict, secret: str) -> str:
    payload = ws_signature_payload(params).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def signed_ws_params(params: dict, secret: str) -> dict:
    out = dict(params)
    out["signature"] = sign_ws_params(out, secret)
    return out
