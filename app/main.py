from typing import Dict, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.services.kline_strategy import VALID_PERIODS, build_kline_payload
from app.services.stock_search import search_stocks
from app.services.stock_data import get_daily_ohlcv, get_intraday_ohlcv, get_realtime_quote
from app.services.trend_analyzer import analyze_stock_trend
from app.services.watchlist_store import load_watchlist, remove_item, select_item, upsert_item

app = FastAPI(title="Stock Daily API")


class AnalyzeRequest(BaseModel):
    symbol: str
    timeframe: str = "day"
    data: List[Dict] = Field(default_factory=list)


class WatchlistItemPayload(BaseModel):
    symbol: str
    name: str | None = None


class WatchlistSelectPayload(BaseModel):
    symbol: str


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
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


@app.get("/api/kline")
def get_kline(
    code: str = Query(..., description="A-share code, e.g. 600519"),
    period: str = Query(default="daily", description="daily or 1/5/15/30/60"),
) -> Dict:
    if period not in VALID_PERIODS:
        allowed = ", ".join(sorted(VALID_PERIODS))
        raise HTTPException(status_code=422, detail=f"invalid period, allowed: {allowed}")
    return build_kline_payload(symbol=code, period=period)


@app.get("/watchlist")
def get_watchlist() -> Dict:
    return load_watchlist()


@app.get("/stocks/search")
def search_stock_items(
    q: str = Query(..., min_length=1, description="code/name/pinyin"),
    limit: int = Query(default=20, ge=1, le=50),
) -> Dict:
    return {"items": search_stocks(query=q, limit=limit)}


@app.post("/watchlist/items")
def add_watchlist_item(payload: WatchlistItemPayload) -> Dict:
    return upsert_item(symbol=payload.symbol, name=payload.name)


@app.delete("/watchlist/items/{symbol}")
def delete_watchlist_item(symbol: str) -> Dict:
    return remove_item(symbol=symbol)


@app.put("/watchlist/selected")
def update_watchlist_selected(payload: WatchlistSelectPayload) -> Dict:
    return select_item(symbol=payload.symbol)
