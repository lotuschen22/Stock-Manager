from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

import akshare as ak
import requests
from fastapi import HTTPException

def normalize_cn_symbol(symbol: str) -> Tuple[str, str]:
    s = str(symbol or "").strip().lower()
    if not s:
        raise HTTPException(status_code=422, detail="symbol is required")

    market_prefix = ""
    if s.startswith(("sh", "sz")):
        market_prefix = s[:2]
        s = s[2:]
    if not s:
        raise HTTPException(status_code=422, detail="invalid symbol")
    return s, market_prefix


def _with_exchange_prefix(code: str, market_prefix: str) -> str:
    if market_prefix in {"sh", "sz"}:
        return f"{market_prefix}{code}"
    if code.startswith(("6", "9")):
        return f"sh{code}"
    if code.startswith(("0", "3")):
        return f"sz{code}"
    return code


def get_daily_ohlcv(symbol: str, limit: int = 30) -> List[Dict]:
    if limit <= 0:
        raise HTTPException(status_code=422, detail="limit must be greater than 0")

    code, market_prefix = normalize_cn_symbol(symbol)
    end_date = datetime.now().strftime("%Y%m%d")
    lookback_days = max(limit * 5, 120)
    start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    symbol_ex = _with_exchange_prefix(code, market_prefix)

    errors: List[str] = []
    df = None

    try:
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="",
        )
    except Exception as exc:
        errors.append(f"stock_zh_a_hist: {exc}")

    if df is None or df.empty:
        try:
            df = ak.stock_zh_a_daily(symbol=symbol_ex, adjust="")
            if df is not None and not df.empty:
                if "date" in df.columns:
                    dt = df["date"].astype(str)
                    df = df[(dt >= f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}") & (dt <= f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}")]
        except Exception as exc:
            errors.append(f"stock_zh_a_daily: {exc}")

    if df is None or df.empty:
        try:
            df = ak.stock_zh_a_hist_tx(symbol=symbol_ex)
            if df is not None and not df.empty and "date" in df.columns:
                dt = df["date"].astype(str)
                df = df[(dt >= f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}") & (dt <= f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}")]
        except Exception as exc:
            errors.append(f"stock_zh_a_hist_tx: {exc}")

    if df is None or df.empty:
        detail = errors[-1] if errors else "no stock data found"
        raise HTTPException(status_code=400, detail=f"failed to fetch daily data: {detail}")

    df = df.tail(limit)
    rename_map = _detect_rename_map(df)
    normalized = df.rename(columns=rename_map)
    if "date" not in normalized.columns and "datetime" in normalized.columns:
        normalized["date"] = normalized["datetime"]
    required = ["date", "open", "close", "high", "low", "volume"]
    missing = [col for col in required if col not in normalized.columns]
    if missing:
        raise HTTPException(status_code=500, detail=f"missing expected columns: {', '.join(missing)}")

    rows = normalized[required].to_dict(orient="records")
    return [_normalize_record(_normalize_date_field(row, "date")) for row in rows]


def get_realtime_quote(symbol: str) -> Dict:
    code, market_prefix = normalize_cn_symbol(symbol)
    symbol_ex = _with_exchange_prefix(code, market_prefix)

    quote = _fetch_realtime_quote_tencent(symbol_ex=symbol_ex)
    if quote is None:
        raise HTTPException(status_code=503, detail="realtime source unavailable")
    return _normalize_record(quote)


def get_intraday_ohlcv(symbol: str, period: str = "1", limit: int = 240) -> List[Dict]:
    if period not in {"1", "5"}:
        raise HTTPException(status_code=422, detail="period must be 1 or 5")
    if limit <= 0:
        raise HTTPException(status_code=422, detail="limit must be greater than 0")

    code, market_prefix = normalize_cn_symbol(symbol)
    symbol_ex = _with_exchange_prefix(code, market_prefix)

    now = datetime.now()
    start_date = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    end_date = now.strftime("%Y-%m-%d %H:%M:%S")

    errors: List[str] = []
    df = _fetch_hist_min_em_with_retry(
        code=code,
        period=period,
        start_date=start_date,
        end_date=end_date,
        errors=errors,
    )
    if df is None or df.empty:
        df = _fetch_sina_minute_with_retry(symbol_ex=symbol_ex, period=period, errors=errors)

    if df is None or df.empty:
        detail = "; ".join(errors) if errors else "no intraday data found"
        raise HTTPException(status_code=400, detail=f"failed to fetch intraday data: {detail}")

    rename_map = _detect_rename_map(df)
    normalized = df.rename(columns=rename_map)

    required = ["datetime", "open", "close", "high", "low", "volume"]
    missing = [col for col in required if col not in normalized.columns]
    if missing:
        # Try to map date column as datetime when needed.
        if "date" in normalized.columns and "datetime" not in normalized.columns:
            normalized["datetime"] = normalized["date"]
            missing = [col for col in required if col not in normalized.columns]
        if missing:
            raise HTTPException(status_code=500, detail=f"missing expected columns: {', '.join(missing)}")

    selected = [col for col in [*required, "amount", "avg_price"] if col in normalized.columns]
    rows = normalized[selected].to_dict(orient="records")
    rows = _keep_latest_trading_day(rows)
    rows = _sanitize_intraday_rows(rows)
    rows = rows[-limit:]
    return [_normalize_record(_normalize_date_field(row, "datetime")) for row in rows]


def get_timeline_data(symbol: str, limit: int = 241) -> List[Dict]:
    if limit <= 0:
        raise HTTPException(status_code=422, detail="limit must be greater than 0")

    rows = get_intraday_ohlcv(symbol=symbol, period="1", limit=max(limit, 241))
    if not rows:
        return []

    out: List[Dict[str, Any]] = []
    vol_sum = 0.0
    amt_sum = 0.0
    for row in rows:
        price = _to_float_or_none(row.get("close"))
        if price is None:
            continue
        volume = _to_float_or_none(row.get("volume")) or 0.0
        amount = _to_float_or_none(row.get("amount"))
        if amount is not None and amount > 0:
            amt_sum += amount
            vol_sum += max(volume, 0.0)
            avg_price = (amt_sum / vol_sum) if vol_sum > 0 else price
        else:
            avg_raw = _to_float_or_none(row.get("avg_price"))
            avg_price = avg_raw if avg_raw is not None and avg_raw > 0 else price
        out.append(
            {
                "datetime": str(row.get("datetime") or ""),
                "price": price,
                "avg_price": avg_price,
                "volume": volume,
            }
        )

    return out[-limit:]


def _fetch_hist_min_em_with_retry(
    code: str,
    period: str,
    start_date: str,
    end_date: str,
    errors: List[str],
):
    for _ in range(2):
        try:
            df = ak.stock_zh_a_hist_min_em(
                symbol=code,
                period=period,
                start_date=start_date,
                end_date=end_date,
                adjust="",
            )
            if df is not None and not df.empty:
                return df
            errors.append("stock_zh_a_hist_min_em: empty result")
        except Exception as exc:
            errors.append(f"stock_zh_a_hist_min_em: {exc}")
        time.sleep(0.25)
    return None


def _fetch_sina_minute_with_retry(symbol_ex: str, period: str, errors: List[str]):
    for _ in range(3):
        try:
            df = ak.stock_zh_a_minute(
                symbol=symbol_ex,
                period=period,
                adjust="",
            )
            if df is not None and not df.empty:
                return df
            errors.append("stock_zh_a_minute: empty result")
        except Exception as exc:
            errors.append(f"stock_zh_a_minute: {exc}")
        time.sleep(0.2)
    return None


def _detect_rename_map(df) -> Dict[str, str]:
    columns = list(df.columns)
    lowered = {str(col).strip().lower(): col for col in columns}

    def pick(*candidates: str) -> str | None:
        for item in candidates:
            if item in lowered:
                return lowered[item]
        return None

    rename_map: Dict[str, str] = {}
    dt_col = pick("datetime", "date", "time", "day", "timestamp", "时间", "日期")
    open_col = pick("open", "开盘")
    close_col = pick("close", "收盘")
    high_col = pick("high", "最高")
    low_col = pick("low", "最低")
    volume_col = pick("volume", "vol", "成交量")
    amount_col = pick("amount", "成交额")
    avg_col = pick("avg_price", "均价")

    if dt_col:
        rename_map[dt_col] = "datetime"
    if open_col:
        rename_map[open_col] = "open"
    if close_col:
        rename_map[close_col] = "close"
    if high_col:
        rename_map[high_col] = "high"
    if low_col:
        rename_map[low_col] = "low"
    if volume_col:
        rename_map[volume_col] = "volume"
    if amount_col:
        rename_map[amount_col] = "amount"
    if avg_col:
        rename_map[avg_col] = "avg_price"

    # Fallback by position for common schema.
    if "open" not in rename_map.values() and len(columns) >= 2:
        rename_map[columns[1]] = "open"
    if "close" not in rename_map.values() and len(columns) >= 3:
        rename_map[columns[2]] = "close"
    if "high" not in rename_map.values() and len(columns) >= 4:
        rename_map[columns[3]] = "high"
    if "low" not in rename_map.values() and len(columns) >= 5:
        rename_map[columns[4]] = "low"
    if "volume" not in rename_map.values() and len(columns) >= 6:
        rename_map[columns[5]] = "volume"
    if "datetime" not in rename_map.values() and "date" not in rename_map.values() and len(columns) >= 1:
        rename_map[columns[0]] = "datetime"

    return rename_map


def _normalize_date_field(record: Dict[str, Any], key: str) -> Dict[str, Any]:
    out = dict(record)
    if key in out and out[key] is not None:
        text = str(out[key]).strip()
        if len(text) == 10:
            out[key] = f"{text} 00:00:00"
        else:
            out[key] = text
    return out


def _normalize_record(record: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for key, value in record.items():
        if value != value:  # NaN
            normalized[key] = None
            continue
        normalized[key] = value
    return normalized


def _keep_latest_trading_day(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not rows:
        return rows
    latest_dt = str(rows[-1].get("datetime", "") or rows[-1].get("date", ""))
    if len(latest_dt) < 10:
        return rows
    latest_day = latest_dt[:10]
    return [row for row in rows if str(row.get("datetime", row.get("date", ""))).startswith(latest_day)]


def _sanitize_intraday_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    prev_close: float | None = None

    for row in rows:
        dt = row.get("datetime") or row.get("date")
        close_v = _as_pos_float(row.get("close"))
        if close_v is None:
            continue

        open_v = _as_pos_float(row.get("open"))
        high_v = _as_pos_float(row.get("high"))
        low_v = _as_pos_float(row.get("low"))

        if open_v is None:
            open_v = prev_close if prev_close is not None else close_v
        if high_v is None:
            high_v = max(open_v, close_v)
        if low_v is None:
            low_v = min(open_v, close_v)

        high_v = max(high_v, open_v, close_v, low_v)
        low_v = min(low_v, open_v, close_v, high_v)

        fixed = dict(row)
        fixed["datetime"] = str(dt)
        fixed["open"] = open_v
        fixed["close"] = close_v
        fixed["high"] = high_v
        fixed["low"] = low_v
        cleaned.append(fixed)
        prev_close = close_v

    return cleaned


def _as_pos_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _fetch_realtime_quote_tencent(symbol_ex: str) -> Dict[str, Any] | None:
    """
    Fast per-symbol quote endpoint.
    Format example: https://qt.gtimg.cn/q=sh600519
    """
    try:
        resp = requests.get(
            f"https://qt.gtimg.cn/q={symbol_ex}",
            timeout=3.5,
            headers={"Referer": "https://finance.qq.com/"},
        )
    except Exception:
        return None
    if resp.status_code != 200:
        return None

    text = str(resp.text or "").strip()
    if "~" not in text:
        return None
    try:
        raw = text.split('="', 1)[1].rsplit('"', 1)[0]
    except Exception:
        return None
    parts = raw.split("~")
    if len(parts) < 40:
        return None

    price = _as_pos_float(parts[3])
    prev_close = _as_pos_float(parts[4])
    day_open = _as_pos_float(parts[5])
    day_high = _as_pos_float(parts[33])
    day_low = _as_pos_float(parts[34])
    if price is None:
        return None

    change = _to_float_or_none(parts[31])
    change_percent = _to_float_or_none(parts[32])
    if change is None and prev_close:
        change = price - prev_close
    if change_percent is None and prev_close:
        change_percent = (price - prev_close) / prev_close * 100.0

    quote_time = _format_tencent_timestamp(parts[30]) or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return {
        "symbol": symbol_ex,
        "name": str(parts[1] or "").strip() or None,
        "price": price,
        "prev_close": prev_close,
        "change": change,
        "change_percent": round(change_percent, 4) if change_percent is not None else None,
        "open": day_open,
        "high": day_high,
        "low": day_low,
        "volume": _to_float_or_none(parts[36]),
        "amount": _to_float_or_none(parts[37]),
        "time": quote_time,
    }


def _to_float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_tencent_timestamp(value: str) -> str:
    text = str(value or "").strip()
    if len(text) != 14 or not text.isdigit():
        return ""
    raw = f"{text[:4]}-{text[4:6]}-{text[6:8]} {text[8:10]}:{text[10:12]}:{text[12:14]}"
    return _normalize_market_quote_time(raw)


def _normalize_market_quote_time(value: str) -> str:
    """
    Keep quote timestamp within regular A-share session boundary.
    After 15:00, display time should stop at 15:00 for that trading day.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text

    close_dt = dt.replace(hour=15, minute=0, second=0, microsecond=0)
    if dt > close_dt:
        return close_dt.strftime("%Y-%m-%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def get_prev_close_reference(symbol: str, day_open: Any = None) -> float | None:
    """
    Resolve previous-close reference used by mainstream quote UIs.
    If today's daily bar is already present, use the prior day's close.
    Otherwise use the latest available daily close.
    """
    try:
        daily_rows = get_daily_ohlcv(symbol=symbol, limit=2)
    except Exception:
        return None

    if not daily_rows:
        return None

    if len(daily_rows) == 1:
        return _as_pos_float(daily_rows[0].get("close"))

    last = daily_rows[-1]
    prev = daily_rows[-2]
    last_close = _as_pos_float(last.get("close"))
    prev_close = _as_pos_float(prev.get("close"))
    last_open = _as_pos_float(last.get("open"))
    day_open_v = _as_pos_float(day_open)

    if day_open_v and last_open:
        # Match today's intraday session to today's daily bar by open price.
        same_session = abs(last_open - day_open_v) <= max(0.02, day_open_v * 0.002)
        if same_session and prev_close:
            return prev_close

    return last_close or prev_close
