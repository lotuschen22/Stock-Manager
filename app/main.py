from typing import Dict, List

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.services.stock_data import get_daily_ohlcv, get_intraday_ohlcv, get_realtime_quote
from app.services.trend_analyzer import analyze_stock_trend

app = FastAPI(title="Stock Daily API")


class AnalyzeRequest(BaseModel):
    symbol: str
    timeframe: str = "day"
    data: List[Dict] = Field(default_factory=list)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/daily/{symbol}")
def get_daily(symbol: str) -> List[Dict]:
    return get_daily_ohlcv(symbol=symbol, limit=30)


@app.get("/realtime/{symbol}")
def get_realtime(symbol: str) -> Dict:
    return get_realtime_quote(symbol=symbol)


@app.get("/analyze/{symbol}")
def analyze(symbol: str) -> Dict:
    daily_data = get_daily_ohlcv(symbol=symbol, limit=30)
    return analyze_stock_trend(daily_data, timeframe="day")


@app.post("/analyze")
def analyze_with_payload(payload: AnalyzeRequest) -> Dict:
    timeframe = payload.timeframe or "day"
    if payload.data:
        data = payload.data
    elif timeframe == "5m":
        data = get_intraday_ohlcv(symbol=payload.symbol, period="5", limit=300)
    elif timeframe in {"time", "1m"}:
        data = get_intraday_ohlcv(symbol=payload.symbol, period="1", limit=240)
    else:
        data = get_daily_ohlcv(symbol=payload.symbol, limit=30)

    return analyze_stock_trend(data, timeframe=timeframe)


@app.get("/intraday/{symbol}")
def get_intraday(symbol: str, period: str = Query(default="1", pattern="^(1|5)$")) -> List[Dict]:
    limit = 240 if period == "1" else 300
    return get_intraday_ohlcv(symbol=symbol, period=period, limit=limit)
