from datetime import datetime
from typing import List, Dict

import akshare as ak
from fastapi import FastAPI, HTTPException

app = FastAPI(title="Stock Daily API")


@app.get("/daily/{symbol}")
def get_daily(symbol: str) -> List[Dict]:
    """
    返回最近30天日线数据，字段：date, open, close, high, low, volume
    symbol 示例：
      - A股：'sh600519' 或 'sz000001'
      - 港股：'00700'
      - 美股：'AAPL'
    """
    try:
        # 获取日线数据（含复权字段等，统一取需要字段）
        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date="",
            end_date="",
            adjust=""
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"数据获取失败: {e}")

    if df is None or df.empty:
        raise HTTPException(status_code=404, detail="未找到该股票数据")

    # 取最近30条
    df = df.tail(30)

    # akshare 返回列名可能是中文，映射到目标字段
    # 常见列：日期, 开盘, 收盘, 最高, 最低, 成交量
    col_map = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
    }

    # 兼容英文列名（若存在）
    for k, v in list(col_map.items()):
        if k not in df.columns and v in df.columns:
            col_map[v] = v

    if not any(k in df.columns for k in col_map.keys()):
        raise HTTPException(status_code=500, detail="数据列结构不符合预期")

    df = df.rename(columns=col_map)

    # 只保留目标字段
    result = df[["date", "open", "close", "high", "low", "volume"]].to_dict(orient="records")
    return result


