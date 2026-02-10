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
        df = ak.stock_zh_a_spot_em()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"数据获取失败: {e}")

    if df is None or df.empty:
        raise HTTPException(status_code=404, detail="未找到实时数据")

    code_column = _pick_column(df.columns.tolist(), ("代码", "symbol", "代码symbol"))
    if code_column is None:
        raise HTTPException(status_code=500, detail="实时数据缺少代码列")

    row = df[df[code_column].astype(str) == s]
    if row.empty:
        raise HTTPException(status_code=404, detail="未找到该股票数据")

    r = row.iloc[0]
    quote = {
        "symbol": (market_prefix + s) if market_prefix else s,
        "name": _pick_value(r, ("名称", "name")),
        "price": _pick_value(r, ("最新价", "price")),
        "open": _pick_value(r, ("今开", "open")),
        "high": _pick_value(r, ("最高", "high")),
        "low": _pick_value(r, ("最低", "low")),
        "volume": _pick_value(r, ("成交量", "volume")),
        "amount": _pick_value(r, ("成交额", "amount")),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return _normalize_record(quote)


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
