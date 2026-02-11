from __future__ import annotations

from datetime import datetime, timedelta
import time
from typing import Dict, List

import akshare as ak
import numpy as np
import pandas as pd
from fastapi import HTTPException


VALID_PERIODS = {"daily", "1", "5", "15", "30", "60"}
NUMERIC_COLUMNS = ["open", "close", "high", "low", "volume"]


def get_stock_data(symbol: str, period: str) -> pd.DataFrame:
    code = _normalize_symbol(symbol)
    symbol_with_exchange = _to_exchange_symbol(symbol)
    if period not in VALID_PERIODS:
        raise HTTPException(
            status_code=422,
            detail="period must be one of: daily, 1, 5, 15, 30, 60",
        )

    try:
        if period == "daily":
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=540)).strftime("%Y%m%d")
            raw = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq",
            )
            df = _normalize_ohlcv_df(raw, time_key="date")
        else:
            try:
                raw = _fetch_minute_data_with_retry(symbol=code, period=period)
            except Exception:
                # Eastmoney minute endpoint can intermittently fail in some networks.
                # Fallback to Sina minute endpoint to keep chart requests available.
                raw = _fetch_minute_data_from_sina(symbol=symbol_with_exchange, period=period)
            df = _normalize_ohlcv_df(raw, time_key="date")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"failed to fetch stock data: {exc}")

    if df.empty:
        raise HTTPException(status_code=404, detail="no stock data found")
    return df.reset_index(drop=True)


def _fetch_minute_data_with_retry(symbol: str, period: str) -> pd.DataFrame:
    # Smaller lookback windows improve Eastmoney stability for minute endpoints.
    lookbacks = {
        "1": [15, 7],
        "5": [45, 20],
        "15": [90, 45],
        "30": [120, 60],
        "60": [180, 90],
    }.get(period, [45, 20])

    errors: List[str] = []
    for days in lookbacks:
        for attempt in range(2):
            now = datetime.now()
            end_time = now.strftime("%Y-%m-%d %H:%M:%S")
            start_time = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            try:
                return ak.stock_zh_a_hist_min_em(
                    symbol=symbol,
                    period=period,
                    start_date=start_time,
                    end_date=end_time,
                    adjust="qfq",
                )
            except Exception as exc:
                errors.append(str(exc))
                time.sleep(0.35)

    raise RuntimeError(errors[-1] if errors else "minute data fetch failed")


def _fetch_minute_data_from_sina(symbol: str, period: str) -> pd.DataFrame:
    try:
        return ak.stock_zh_a_minute(
            symbol=symbol,
            period=period,
            adjust="",
        )
    except Exception as exc:
        raise RuntimeError(f"sina minute fallback failed: {exc}")


def calculate_macd(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"]
    ema_fast = close.ewm(span=12, adjust=False).mean()
    ema_slow = close.ewm(span=26, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_hist = dif - dea

    out = df.copy()
    out["dif"] = dif.astype(float)
    out["dea"] = dea.astype(float)
    out["macd_hist"] = macd_hist.astype(float)
    return out


def calculate_kdj(df: pd.DataFrame, period: int = 9) -> pd.DataFrame:
    low_n = df["low"].rolling(window=period, min_periods=1).min()
    high_n = df["high"].rolling(window=period, min_periods=1).max()

    denominator = (high_n - low_n).replace(0, np.nan)
    rsv = ((df["close"] - low_n) / denominator * 100).fillna(50.0)

    k_values: List[float] = []
    d_values: List[float] = []
    prev_k = 50.0
    prev_d = 50.0
    for value in rsv.tolist():
        k = (2.0 / 3.0) * prev_k + (1.0 / 3.0) * float(value)
        d = (2.0 / 3.0) * prev_d + (1.0 / 3.0) * k
        k_values.append(k)
        d_values.append(d)
        prev_k = k
        prev_d = d

    out = df.copy()
    out["k"] = pd.Series(k_values, dtype="float64")
    out["d"] = pd.Series(d_values, dtype="float64")
    out["j"] = 3.0 * out["k"] - 2.0 * out["d"]
    return out


def analyze_strategy(df: pd.DataFrame) -> List[Dict]:
    signals: List[Dict] = []
    if len(df) < 40:
        return signals

    high_price = df["high"]
    low_price = df["low"]
    close_price = df["close"]
    hist = df["macd_hist"]
    j_line = df["j"]

    for idx in range(39, len(df)):
        current_window = slice(idx - 19, idx + 1)
        previous_window = slice(idx - 39, idx - 19)

        current_price_max = float(high_price.iloc[current_window].max())
        previous_price_max = float(high_price.iloc[previous_window].max())
        current_hist_max = float(hist.iloc[current_window].max())
        previous_hist_max = float(hist.iloc[previous_window].max())

        top_divergence = (
            float(high_price.iloc[idx]) >= current_price_max
            and current_price_max > previous_price_max
            and current_hist_max < previous_hist_max
        )

        current_price_min = float(low_price.iloc[current_window].min())
        previous_price_min = float(low_price.iloc[previous_window].min())
        current_hist_min = float(hist.iloc[current_window].min())
        previous_hist_min = float(hist.iloc[previous_window].min())

        bottom_divergence = (
            float(low_price.iloc[idx]) <= current_price_min
            and current_price_min < previous_price_min
            and current_hist_min > previous_hist_min
        )

        j_val = float(j_line.iloc[idx])
        dt = str(df.iloc[idx]["date"])
        close_val = float(close_price.iloc[idx])

        if top_divergence and j_val > 80:
            signals.append(
                {
                    "date": dt,
                    "type": "sell",
                    "price": close_val,
                    "desc": "MACD bearish divergence + KDJ overbought (J>80)",
                }
            )
        elif bottom_divergence and j_val < 20:
            signals.append(
                {
                    "date": dt,
                    "type": "buy",
                    "price": close_val,
                    "desc": "MACD bullish divergence + KDJ oversold (J<20)",
                }
            )

    return signals


def build_kline_payload(symbol: str, period: str) -> Dict:
    df = get_stock_data(symbol=symbol, period=period)
    df = calculate_macd(df)
    df = calculate_kdj(df, period=9)
    signals = analyze_strategy(df)

    output_columns = [
        "date",
        "open",
        "close",
        "high",
        "low",
        "volume",
        "dif",
        "dea",
        "macd_hist",
        "k",
        "d",
        "j",
    ]

    data = []
    for item in df[output_columns].to_dict(orient="records"):
        record = {}
        for key, value in item.items():
            if pd.isna(value):
                record[key] = None
            elif key == "date":
                record[key] = str(value)
            else:
                record[key] = float(value)
        data.append(record)

    return {"data": data, "signals": signals}


def _normalize_symbol(symbol: str) -> str:
    code = symbol.strip().lower()
    if code.startswith(("sh", "sz")):
        code = code[2:]
    if not code:
        raise HTTPException(status_code=422, detail="code is required")
    return code


def _to_exchange_symbol(symbol: str) -> str:
    text = symbol.strip().lower()
    if text.startswith(("sh", "sz")) and len(text) >= 8:
        return text

    code = _normalize_symbol(text)
    if code.startswith(("6", "9")):
        return f"sh{code}"
    if code.startswith(("0", "3")):
        return f"sz{code}"
    raise HTTPException(status_code=422, detail="unable to infer exchange from code")


def _normalize_ohlcv_df(df: pd.DataFrame, time_key: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=[time_key, *NUMERIC_COLUMNS])

    rename_map = _detect_rename_map(df)

    out = df.rename(columns=rename_map)[[time_key, *NUMERIC_COLUMNS]].copy()
    out[time_key] = pd.to_datetime(out[time_key], errors="coerce")
    out = out.dropna(subset=[time_key])
    for col in NUMERIC_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="coerce").astype(float)
    out = out.dropna(subset=NUMERIC_COLUMNS)
    out = out.sort_values(time_key).reset_index(drop=True)
    out[time_key] = out[time_key].dt.strftime("%Y-%m-%d %H:%M:%S")
    if all(ts.endswith("00:00:00") for ts in out[time_key].head(min(20, len(out)))):
        out[time_key] = out[time_key].str.slice(0, 10)
    return out


def _detect_rename_map(df: pd.DataFrame) -> Dict[str, str]:
    columns = list(df.columns)
    lowered = {str(col).strip().lower(): col for col in columns}

    def pick(*candidates: str) -> str | None:
        for item in candidates:
            if item in lowered:
                return lowered[item]
        return None

    rename_map: Dict[str, str] = {}
    time_col = pick("date", "datetime", "time", "timestamp")
    open_col = pick("open")
    close_col = pick("close")
    high_col = pick("high")
    low_col = pick("low")
    volume_col = pick("volume", "vol")

    # Fallback to common A-share ordering: date/open/close/high/low/volume
    if not time_col and len(columns) >= 1:
        time_col = columns[0]
    if not open_col and len(columns) >= 2:
        open_col = columns[1]
    if not close_col and len(columns) >= 3:
        close_col = columns[2]
    if not high_col and len(columns) >= 4:
        high_col = columns[3]
    if not low_col and len(columns) >= 5:
        low_col = columns[4]
    if not volume_col and len(columns) >= 6:
        volume_col = columns[5]

    required = {
        "date": time_col,
        "open": open_col,
        "close": close_col,
        "high": high_col,
        "low": low_col,
        "volume": volume_col,
    }
    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise HTTPException(status_code=500, detail=f"missing expected columns: {', '.join(missing)}")

    for target, source in required.items():
        rename_map[source] = target
    return rename_map
