from __future__ import annotations

import threading
import time
import unicodedata
from typing import Dict, List

import akshare as ak
from fastapi import HTTPException

try:
    from pypinyin import lazy_pinyin
except Exception:  # pragma: no cover
    lazy_pinyin = None

_CACHE_TTL_SECONDS = 6 * 60 * 60
_CACHE: Dict[str, object] = {
    "expires_at": 0.0,
    "items": [],
}
_CACHE_LOCK = threading.Lock()
_CACHE_REFRESHING = False


def search_stocks(query: str, limit: int = 20) -> List[Dict]:
    q = _normalize_query(query)
    if not q:
        return []

    universe = _load_universe()
    scored = []
    for item in universe:
        score = _score_item(item, q)
        if score <= 0:
            continue
        scored.append((score, item))

    scored.sort(key=lambda pair: (-pair[0], pair[1]["code"]))
    result = []
    for _, item in scored[:limit]:
        result.append(
            {
                "symbol": item["symbol"],
                "code": item["code"],
                "name": item["name"],
            }
        )
    return result


def _load_universe() -> List[Dict]:
    now = time.time()
    with _CACHE_LOCK:
        expires_at = float(_CACHE.get("expires_at") or 0.0)
        cached_items = _CACHE.get("items")
        has_cached = isinstance(cached_items, list) and bool(cached_items)
        if has_cached and now < expires_at:
            return cached_items
        if has_cached:
            _ensure_background_refresh()
            return cached_items

    items = _fetch_universe()
    with _CACHE_LOCK:
        _CACHE["items"] = items
        _CACHE["expires_at"] = now + _CACHE_TTL_SECONDS
    return items


def _ensure_background_refresh() -> None:
    global _CACHE_REFRESHING
    with _CACHE_LOCK:
        if _CACHE_REFRESHING:
            return
        _CACHE_REFRESHING = True

    thread = threading.Thread(target=_refresh_universe_worker, daemon=True)
    thread.start()


def _refresh_universe_worker() -> None:
    global _CACHE_REFRESHING
    try:
        items = _fetch_universe()
        with _CACHE_LOCK:
            _CACHE["items"] = items
            _CACHE["expires_at"] = time.time() + _CACHE_TTL_SECONDS
    except Exception:
        pass
    finally:
        with _CACHE_LOCK:
            _CACHE_REFRESHING = False


def warm_stock_universe() -> None:
    try:
        _load_universe()
    except Exception:
        # Warm-up should not block service startup.
        return


def _fetch_universe() -> List[Dict]:
    rows: List[Dict] = []
    try:
        df = ak.stock_info_a_code_name()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"failed to load stock list: {exc}")

    columns = [str(col).strip().lower() for col in df.columns]
    code_col = None
    name_col = None
    for idx, col in enumerate(columns):
        if col in {"code", "代码"} and code_col is None:
            code_col = df.columns[idx]
        if col in {"name", "名称"} and name_col is None:
            name_col = df.columns[idx]

    if code_col is None or name_col is None:
        if len(df.columns) >= 2:
            code_col = df.columns[0]
            name_col = df.columns[1]
        else:
            raise HTTPException(status_code=500, detail="unexpected stock list schema")

    for _, row in df.iterrows():
        code = str(row.get(code_col) or "").strip()
        name = str(row.get(name_col) or "").strip()
        if len(code) != 6 or not code.isdigit() or not name:
            continue
        symbol = _symbol_from_code(code)
        if not symbol:
            continue
        full_pinyin, abbr_pinyin = _to_pinyin(name)
        rows.append(
            {
                "code": code,
                "symbol": symbol,
                "name": name,
                "name_lower": name.lower(),
                "name_norm": _normalize_text(name),
                "pinyin": full_pinyin,
                "abbr": abbr_pinyin,
            }
        )

    if not rows:
        raise HTTPException(status_code=503, detail="stock universe is empty")
    return rows


def _symbol_from_code(code: str) -> str:
    if code.startswith(("6", "9")):
        return f"sh{code}"
    if code.startswith(("0", "3")):
        return f"sz{code}"
    return ""


def _to_pinyin(text: str) -> tuple[str, str]:
    if lazy_pinyin is None:
        return "", ""
    parts = lazy_pinyin(text)
    if not parts:
        return "", ""
    full = "".join(parts).lower()
    abbr = "".join(p[0] for p in parts if p).lower()
    return full, abbr


def _score_item(item: Dict, q: str) -> int:
    code = str(item["code"])
    symbol = str(item["symbol"])
    name_lower = str(item["name_lower"])
    name_norm = str(item.get("name_norm") or "")
    pinyin = str(item["pinyin"])
    abbr = str(item["abbr"])

    score = 0
    if code == q or symbol == q:
        score = max(score, 1200)
    elif code.startswith(q):
        score = max(score, 1100)
    elif q in code:
        score = max(score, 1000)
    elif symbol.startswith(q):
        score = max(score, 990)

    if name_lower == q:
        score = max(score, 950)
    elif name_lower.startswith(q):
        score = max(score, 900)
    elif q in name_lower:
        score = max(score, 850)
    elif name_norm:
        if name_norm == q:
            score = max(score, 950)
        elif name_norm.startswith(q):
            score = max(score, 900)
        elif q in name_norm:
            score = max(score, 850)

    if pinyin:
        if pinyin == q:
            score = max(score, 820)
        elif pinyin.startswith(q):
            score = max(score, 780)
        elif q in pinyin:
            score = max(score, 740)
    if abbr:
        if abbr == q:
            score = max(score, 760)
        elif abbr.startswith(q):
            score = max(score, 720)

    return score


def _normalize_query(text: str) -> str:
    return _normalize_text(text)


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = "".join(ch for ch in normalized if not ch.isspace())
    return normalized.strip().lower()
