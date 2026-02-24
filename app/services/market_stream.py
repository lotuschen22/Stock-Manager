from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Dict, List

import pandas as pd
from fastapi import HTTPException

from app.services.kline_strategy import analyze_strategy, calculate_kdj, calculate_macd
from app.services.stock_data import get_intraday_ohlcv, get_realtime_quote
from app.services.watchlist_store import normalize_symbol_input

_SIGNAL_REFRESH_INTERVAL_SECONDS = 20.0
_SIGNAL_CACHE_LOCK = threading.Lock()
_SIGNAL_CACHE: Dict[str, Dict[str, Any]] = {}
_SIGNAL_REFRESHING: set[str] = set()


def build_market_snapshot(symbol: str, timeframe: str = "1", include_detail: bool = True) -> Dict[str, Any]:
    period = str(timeframe or "1")
    if period not in {"1", "5"}:
        raise HTTPException(status_code=422, detail="timeframe must be 1 or 5")

    normalized_symbol = normalize_symbol_input(symbol)
    quote = get_realtime_quote(normalized_symbol)
    latest_price = _to_float(quote.get("price"))
    change_percent = _to_float(quote.get("change_percent"))
    updated_at = str(quote.get("time") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    signal_payload = _get_signal_cached(symbol=normalized_symbol, timeframe=period)
    signal = signal_payload.get("signal")
    signal_ts = signal_payload.get("signal_ts")

    payload: Dict[str, Any] = {
        "type": "market",
        "symbol": normalized_symbol,
        "timeframe": period,
        "price": latest_price,
        "change_percent": round(change_percent, 4),
        "signal": signal,
        "signal_ts": signal_ts,
        "is_closed": False,
        "updated_at": updated_at,
    }
    if include_detail:
        payload.update(
            {
                "prev_close": quote.get("prev_close"),
                "change": quote.get("change"),
                "open": quote.get("open"),
                "high": quote.get("high"),
                "low": quote.get("low"),
                "volume": quote.get("volume"),
            }
        )
    return payload


def _get_signal_cached(symbol: str, timeframe: str) -> Dict[str, Any]:
    key = f"{symbol}:{timeframe}"
    now = time.time()

    with _SIGNAL_CACHE_LOCK:
        entry = _SIGNAL_CACHE.get(key) or {}
        signal = entry.get("signal")
        signal_ts = entry.get("signal_ts")
        refreshed_at = float(entry.get("refreshed_at") or 0.0)
        needs_refresh = (now - refreshed_at) >= _SIGNAL_REFRESH_INTERVAL_SECONDS
        if not needs_refresh:
            return {"signal": signal, "signal_ts": signal_ts}
        if key in _SIGNAL_REFRESHING:
            return {"signal": signal, "signal_ts": signal_ts}
        _SIGNAL_REFRESHING.add(key)

    thread = threading.Thread(
        target=_refresh_signal_worker,
        args=(key, symbol, timeframe),
        daemon=True,
    )
    thread.start()
    return {"signal": signal, "signal_ts": signal_ts}


def _refresh_signal_worker(key: str, symbol: str, timeframe: str) -> None:
    next_signal = None
    next_signal_ts = None
    try:
        limit = 240 if timeframe == "1" else 300
        rows = get_intraday_ohlcv(symbol=symbol, period=timeframe, limit=limit)
        signal_payload = _resolve_latest_signal(rows)
        next_signal = signal_payload.get("signal")
        next_signal_ts = signal_payload.get("signal_ts")
    except Exception:
        # Signal is non-critical for quote freshness.
        pass

    with _SIGNAL_CACHE_LOCK:
        _SIGNAL_CACHE[key] = {
            "signal": next_signal,
            "signal_ts": next_signal_ts,
            "refreshed_at": time.time(),
        }
        _SIGNAL_REFRESHING.discard(key)


def _resolve_latest_signal(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(rows) < 40:
        return {"signal": None, "signal_ts": None}

    df = pd.DataFrame(
        {
            "date": [str(item.get("datetime") or item.get("date") or "") for item in rows],
            "open": [_to_float(item.get("open")) for item in rows],
            "close": [_to_float(item.get("close")) for item in rows],
            "high": [_to_float(item.get("high")) for item in rows],
            "low": [_to_float(item.get("low")) for item in rows],
            "volume": [_to_float(item.get("volume")) for item in rows],
        }
    )
    df = df.replace([float("inf"), float("-inf")], pd.NA).dropna()
    if len(df) < 40:
        return {"signal": None, "signal_ts": None}

    with_macd = calculate_macd(df)
    with_kdj = calculate_kdj(with_macd, period=9)
    signals = analyze_strategy(with_kdj)
    if not signals:
        return {"signal": None, "signal_ts": None}

    latest_bar_ts = str(df.iloc[-1]["date"])
    latest_signal = signals[-1]
    latest_signal_ts = str(latest_signal.get("date") or "")
    if latest_signal_ts != latest_bar_ts:
        return {"signal": None, "signal_ts": None}

    signal_type = str(latest_signal.get("type") or "").lower()
    if signal_type == "buy":
        return {"signal": "B", "signal_ts": latest_signal_ts}
    if signal_type == "sell":
        return {"signal": "S", "signal_ts": latest_signal_ts}
    return {"signal": None, "signal_ts": None}


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
