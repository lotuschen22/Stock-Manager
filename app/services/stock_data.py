from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

import akshare as ak
from fastapi import HTTPException


def normalize_cn_symbol(symbol: str) -> Tuple[str, str]:
    s = symbol.strip()
    if not s:
        raise HTTPException(status_code=422, detail="symbol is required")

    market_prefix = ""
    if s.lower().startswith(("sh", "sz")):
        market_prefix = s[:2].lower()
        s = s[2:]
    if not s:
        raise HTTPException(status_code=422, detail="invalid symbol")

    return s, market_prefix


def get_daily_ohlcv(symbol: str, limit: int = 30) -> List[Dict]:
    if limit <= 0:
        raise HTTPException(status_code=422, detail="limit must be greater than 0")

    s, _ = normalize_cn_symbol(symbol)
    end_date = datetime.now().strftime("%Y%m%d")
    lookback_days = max(limit * 3, 60)
    start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")

    try:
        df = ak.stock_zh_a_hist(
            symbol=s,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="",
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"数据获取失败: {e}")

    if df is None or df.empty:
        raise HTTPException(status_code=404, detail="未找到该股票数据")

    df = df.tail(limit)
    expected_columns = {
        "date": ("日期", "date"),
        "open": ("开盘", "open"),
        "close": ("收盘", "close"),
        "high": ("最高", "high"),
        "low": ("最低", "low"),
        "volume": ("成交量", "volume"),
    }

    rename_map: Dict[str, str] = {}
    for standard_name, aliases in expected_columns.items():
        matched = next((col for col in aliases if col in df.columns), None)
        if matched is None:
            raise HTTPException(status_code=500, detail=f"missing expected column: {standard_name}")
        rename_map[matched] = standard_name

    df = df.rename(columns=rename_map)
    data = df[["date", "open", "close", "high", "low", "volume"]].to_dict(orient="records")
    return [_normalize_record(row) for row in data]


def get_realtime_quote(symbol: str) -> Dict:
    s, market_prefix = normalize_cn_symbol(symbol)

    try:
        rows = get_intraday_ohlcv(symbol=(market_prefix + s) if market_prefix else s, period="1", limit=240)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"数据获取失败: {e}")

    if not rows:
        raise HTTPException(status_code=404, detail="未找到实时数据")

    day_open = rows[0].get("open")
    day_high = max(float(item.get("high") or 0) for item in rows)
    day_low = min(float(item.get("low") or 0) for item in rows)
    day_volume = sum(float(item.get("volume") or 0) for item in rows)
    day_amount = sum(float(item.get("amount") or 0) for item in rows if item.get("amount") is not None)
    latest = rows[-1]
    quote = {
        "symbol": (market_prefix + s) if market_prefix else s,
        "name": _get_stock_name(s),
        "price": latest.get("close"),
        "open": day_open,
        "high": day_high,
        "low": day_low,
        "volume": day_volume,
        "amount": day_amount,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return _normalize_record(quote)


def get_intraday_ohlcv(symbol: str, period: str = "1", limit: int = 240) -> List[Dict]:
    if period not in {"1", "5"}:
        raise HTTPException(status_code=422, detail="period must be 1 or 5")
    if limit <= 0:
        raise HTTPException(status_code=422, detail="limit must be greater than 0")

    s, _ = normalize_cn_symbol(symbol)
    now = datetime.now()
    start_date = (now - timedelta(days=5)).strftime("%Y-%m-%d 09:30:00")
    end_date = now.strftime("%Y-%m-%d %H:%M:%S")

    try:
        df = ak.stock_zh_a_hist_min_em(
            symbol=s,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust="",
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"数据获取失败: {e}")

    if df is None or df.empty:
        raise HTTPException(status_code=404, detail="未找到该股票分钟数据")

    expected_columns = {
        "datetime": ("时间", "datetime"),
        "open": ("开盘", "open"),
        "close": ("收盘", "close"),
        "high": ("最高", "high"),
        "low": ("最低", "low"),
        "volume": ("成交量", "volume"),
        "amount": ("成交额", "amount"),
        "avg_price": ("均价", "avg_price"),
    }

    rename_map: Dict[str, str] = {}
    for standard_name, aliases in expected_columns.items():
        matched = next((col for col in aliases if col in df.columns), None)
        if matched is not None:
            rename_map[matched] = standard_name

    df = df.rename(columns=rename_map)
    required = ["datetime", "open", "close", "high", "low", "volume"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise HTTPException(status_code=500, detail=f"missing expected columns: {', '.join(missing)}")

    selected_columns = [col for col in required + ["amount", "avg_price"] if col in df.columns]
    rows = df[selected_columns].to_dict(orient="records")
    rows = _keep_latest_trading_day(rows)
    rows = _sanitize_intraday_rows(rows)
    rows = rows[-limit:]
    return [_normalize_record(row) for row in rows]


def _pick_column(columns: List[str], aliases: Tuple[str, ...]) -> str | None:
    for alias in aliases:
        if alias in columns:
            return alias
    return None


def _pick_value(row: Any, aliases: Tuple[str, ...]) -> Any:
    for alias in aliases:
        if alias in row.index:
            return row.get(alias)
    return None


def _normalize_record(record: Dict[str, Any]) -> Dict[str, Any]:
    # Replace NaN-like values with None to keep JSON serialization safe.
    normalized: Dict[str, Any] = {}
    for key, value in record.items():
        if value != value:  # NaN check without importing pandas/numpy
            normalized[key] = None
            continue
        normalized[key] = value
    return normalized


def _keep_latest_trading_day(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not rows:
        return rows
    latest_dt = str(rows[-1].get("datetime", ""))
    if len(latest_dt) < 10:
        return rows
    latest_day = latest_dt[:10]
    return [row for row in rows if str(row.get("datetime", "")).startswith(latest_day)]


def _sanitize_intraday_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    prev_close: float | None = None

    for row in rows:
        dt = row.get("datetime")
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

        # Keep OHLC ordering valid.
        high_v = max(high_v, open_v, close_v, low_v)
        low_v = min(low_v, open_v, close_v, high_v)

        fixed = dict(row)
        fixed["datetime"] = dt
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


def _get_stock_name(symbol: str) -> str | None:
    try:
        df = ak.stock_individual_info_em(symbol=symbol)
    except Exception:
        return None

    if df is None or df.empty:
        return None
    item_col = _pick_column(df.columns.tolist(), ("item", "项目"))
    value_col = _pick_column(df.columns.tolist(), ("value", "值"))
    if not item_col or not value_col:
        return None

    row = df[df[item_col].astype(str).isin(["股票简称", "简称", "name"])]
    if row.empty:
        return None
    value = row.iloc[0].get(value_col)
    if value is None:
        return None
    text = str(value).strip()
    return text or None
