import asyncio
import json
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.services.kline_strategy import VALID_PERIODS, build_kline_payload, build_signal_payload
from app.services.market_stream import build_market_snapshot
from app.services.stock_search import search_stocks, warm_stock_universe
from app.services.stock_data import get_daily_ohlcv, get_intraday_ohlcv, get_realtime_quote, get_timeline_data
from app.services.trend_analyzer import analyze_stock_trend
from app.services.watchlist_store import load_watchlist, normalize_symbol_input, remove_item, select_item, upsert_item

app = FastAPI(title="Stock Daily API")

_CACHE_LOCK = threading.Lock()
_ENDPOINT_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_REFRESHING: set[str] = set()
_REALTIME_TTL_SECONDS = 3.0
_KLINE_TTL_SECONDS = 45.0
_TIMELINE_TTL_SECONDS = 3.0


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


@app.on_event("startup")
def _startup_warmups() -> None:
    thread = threading.Thread(target=warm_stock_universe, daemon=True)
    thread.start()


@app.get("/daily/{symbol}")
def get_daily(symbol: str) -> List[Dict]:
    return get_daily_ohlcv(symbol=symbol, limit=30)


@app.get("/realtime/{symbol}")
def get_realtime(symbol: str) -> Dict:
    key = f"realtime:{normalize_symbol_input(symbol)}"
    return _cache_get_strict(
        key=key,
        ttl_seconds=_REALTIME_TTL_SECONDS,
        producer=lambda: get_realtime_quote(symbol=symbol),
    )


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


@app.get("/api/timeline")
def get_timeline(code: str = Query(..., description="A-share code, e.g. 600519")) -> Dict:
    normalized = normalize_symbol_input(code)
    key = f"timeline:{normalized}"
    return _cache_get_or_refresh(
        key=key,
        ttl_seconds=_TIMELINE_TTL_SECONDS,
        producer=lambda: {"items": get_timeline_data(symbol=normalized, limit=241)},
    )


@app.get("/api/kline")
def get_kline(
    code: str = Query(..., description="A-share code, e.g. 600519"),
    period: str = Query(default="daily", description="daily or 1/5/15/30/60"),
) -> Dict:
    if period not in VALID_PERIODS:
        allowed = ", ".join(sorted(VALID_PERIODS))
        raise HTTPException(status_code=422, detail=f"invalid period, allowed: {allowed}")
    normalized = normalize_symbol_input(code)
    key = f"kline:{normalized}:{period}"
    return _cache_get_or_refresh(
        key=key,
        ttl_seconds=_KLINE_TTL_SECONDS,
        producer=lambda: build_kline_payload(symbol=normalized, period=period),
    )


@app.get("/api/signals")
def get_signals(
    code: str = Query(..., description="A-share code, e.g. 600519"),
    period: str = Query(default="1", description="1 or 5"),
) -> Dict:
    if period not in {"1", "5"}:
        raise HTTPException(status_code=422, detail="period must be 1 or 5")
    normalized = normalize_symbol_input(code)
    key = f"signals:{normalized}:{period}"
    return _cache_get_or_refresh(
        key=key,
        ttl_seconds=20.0,
        producer=lambda: build_signal_payload(symbol=normalized, period=period),
    )


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


@app.websocket("/ws/market")
async def market_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    symbols: List[str] = []
    timeframe = "1"
    focus_symbol: str | None = None
    # Avoid sending repetitive error payloads every polling cycle.
    error_cooldown_until: Dict[str, float] = {}

    try:
        while True:
            message = await _try_receive_message(websocket, timeout=0.05)
            if message:
                next_symbols, next_timeframe, next_focus_symbol = _parse_subscribe_message(message)
                if next_symbols is not None:
                    symbols = next_symbols
                if next_timeframe is not None:
                    timeframe = next_timeframe
                if next_focus_symbol is not None:
                    focus_symbol = next_focus_symbol
                phase = _market_phase(datetime.now())
                await websocket.send_json(
                    {
                        "type": "subscribed",
                        "symbols": symbols,
                        "timeframe": timeframe,
                        "focus_symbol": focus_symbol,
                        "market_phase": phase,
                        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )

            if not symbols:
                await asyncio.sleep(0.6)
                continue

            now_dt = datetime.now()
            phase = _market_phase(now_dt)
            if phase != "open":
                # Non-trading session: pause quote polling, keep heartbeat only.
                await websocket.send_json(
                    {
                        "type": "batch",
                        "timeframe": timeframe,
                        "items": [],
                        "market_phase": phase,
                        "ts": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
                await asyncio.sleep(_market_poll_interval_seconds(now_dt))
                continue

            items: List[Dict] = []
            for symbol in symbols:
                try:
                    include_detail = bool(focus_symbol) and symbol == focus_symbol
                    snapshot = await asyncio.to_thread(
                        build_market_snapshot,
                        symbol,
                        timeframe,
                        include_detail,
                    )
                    items.append(snapshot)
                except Exception as exc:  # noqa: BLE001
                    key = f"{symbol}:{timeframe}"
                    now = asyncio.get_running_loop().time()
                    if error_cooldown_until.get(key, 0.0) > now:
                        continue
                    error_cooldown_until[key] = now + 10.0
                    items.append(
                        {
                            "type": "error",
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "detail": str(exc),
                            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        }
                    )

            await websocket.send_json(
                {
                    "type": "batch",
                    "timeframe": timeframe,
                    "items": items,
                    "market_phase": _market_phase(now_dt),
                    "ts": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )

            await asyncio.sleep(_market_poll_interval_seconds(now_dt))
    except WebSocketDisconnect:
        return


async def _try_receive_message(websocket: WebSocket, timeout: float = 0.05) -> Dict | None:
    try:
        packet = await asyncio.wait_for(websocket.receive(), timeout=timeout)
    except asyncio.TimeoutError:
        return None

    packet_type = packet.get("type")
    if packet_type == "websocket.disconnect":
        raise WebSocketDisconnect(code=1000)
    if packet_type != "websocket.receive":
        return None

    if "text" in packet and packet["text"]:
        try:
            return json.loads(packet["text"])
        except Exception:
            return None
    return None


def _parse_subscribe_message(message: Dict) -> tuple[List[str] | None, str | None, str | None]:
    if not isinstance(message, dict):
        return None, None, None
    if str(message.get("type") or "").lower() != "subscribe":
        return None, None, None

    raw_symbols = message.get("symbols")
    symbols: List[str] = []
    if isinstance(raw_symbols, list):
        for item in raw_symbols:
            try:
                normalized = normalize_symbol_input(str(item))
            except HTTPException:
                continue
            if normalized not in symbols:
                symbols.append(normalized)

    timeframe_raw = str(message.get("timeframe") or "1")
    timeframe = timeframe_raw if timeframe_raw in {"1", "5"} else "1"
    focus_symbol = None
    focus_raw = str(message.get("focus_symbol") or "").strip()
    if focus_raw:
        try:
            focus_symbol = normalize_symbol_input(focus_raw)
        except HTTPException:
            focus_symbol = None
    return symbols, timeframe, focus_symbol


def _market_phase(now: datetime) -> str:
    # Weekends are always closed for A-share regular session.
    if now.weekday() >= 5:
        return "closed"
    hhmm = now.strftime("%H:%M")
    if hhmm < "09:00":
        return "preopen"
    if hhmm <= "15:00":
        return "open"
    return "closed"


def _market_poll_interval_seconds(now: datetime) -> float:
    # High frequency during regular session, low frequency otherwise.
    phase = _market_phase(now)
    return 1.0 if phase == "open" else 30.0


def _cache_get_or_refresh(key: str, ttl_seconds: float, producer: Callable[[], Dict]) -> Dict:
    now = time.time()
    now_dt = datetime.now()
    cached_value = None
    cached_at = 0.0
    cached_market_day = ""

    with _CACHE_LOCK:
        entry = _ENDPOINT_CACHE.get(key)
        if entry is not None:
            cached_value = entry.get("value")
            cached_at = float(entry.get("cached_at") or 0.0)
            cached_market_day = str(entry.get("market_day") or "")

    if _is_market_aware_cache_key(key):
        market_day = _market_day_for_cache(now_dt)
        phase = _market_phase(now_dt)
        if cached_value is not None and cached_market_day == market_day:
            # After close, kline/signal data is effectively immutable for the session.
            if phase != "open":
                return cached_value
            if (now - cached_at) < ttl_seconds:
                return cached_value
            _ensure_background_refresh(key=key, producer=producer)
            return cached_value

        # New trading session (or no cache): refresh synchronously.
        value = producer()
        with _CACHE_LOCK:
            _ENDPOINT_CACHE[key] = {
                "value": value,
                "cached_at": time.time(),
                "market_day": market_day,
            }
        return value

    if cached_value is not None and (now - cached_at) < ttl_seconds:
        return cached_value

    if cached_value is not None:
        _ensure_background_refresh(key=key, producer=producer)
        return cached_value

    value = producer()
    with _CACHE_LOCK:
        _ENDPOINT_CACHE[key] = {
            "value": value,
            "cached_at": time.time(),
        }
    return value


def _cache_get_strict(key: str, ttl_seconds: float, producer: Callable[[], Dict]) -> Dict:
    now = time.time()
    with _CACHE_LOCK:
        entry = _ENDPOINT_CACHE.get(key)
        if entry is not None:
            cached_value = entry.get("value")
            cached_at = float(entry.get("cached_at") or 0.0)
            if cached_value is not None and (now - cached_at) < ttl_seconds:
                return cached_value

    value = producer()
    with _CACHE_LOCK:
        _ENDPOINT_CACHE[key] = {
            "value": value,
            "cached_at": time.time(),
        }
    return value


def _ensure_background_refresh(key: str, producer: Callable[[], Dict]) -> None:
    with _CACHE_LOCK:
        if key in _CACHE_REFRESHING:
            return
        _CACHE_REFRESHING.add(key)

    thread = threading.Thread(
        target=_refresh_cache_worker,
        args=(key, producer),
        daemon=True,
    )
    thread.start()


def _refresh_cache_worker(key: str, producer: Callable[[], Dict]) -> None:
    try:
        value = producer()
    except Exception:
        value = None

    with _CACHE_LOCK:
        if value is not None:
            entry = {
                "value": value,
                "cached_at": time.time(),
            }
            if _is_market_aware_cache_key(key):
                entry["market_day"] = _market_day_for_cache(datetime.now())
            _ENDPOINT_CACHE[key] = entry
        _CACHE_REFRESHING.discard(key)


def _is_market_aware_cache_key(key: str) -> bool:
    return key.startswith("kline:") or key.startswith("signals:")


def _market_day_for_cache(now: datetime) -> str:
    # During pre-open, keep using previous trading day's cache; switch at 09:00.
    if now.weekday() >= 5:
        return _previous_trading_day(now).strftime("%Y-%m-%d")
    if now.strftime("%H:%M") < "09:00":
        return _previous_trading_day(now).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")


def _previous_trading_day(now: datetime) -> datetime:
    cur = now - timedelta(days=1)
    while cur.weekday() >= 5:
        cur -= timedelta(days=1)
    return cur
