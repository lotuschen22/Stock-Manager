from typing import Dict, List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.services.stock_data import get_daily_ohlcv, get_realtime_quote
from app.services.trend_analyzer import analyze_stock_trend

app = FastAPI(title="Stock Daily API")

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
    return analyze_stock_trend(daily_data)
