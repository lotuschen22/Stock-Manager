from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Dict, List

from fastapi import HTTPException

DEFAULT_SYMBOL = "sh600549"
_STORE_PATH = Path(__file__).resolve().parent.parent / "data" / "watchlist.json"
_LOCK = threading.Lock()
_SYMBOL_RE = re.compile(r"(sh|sz)?\d{6}", re.IGNORECASE)


def normalize_symbol_input(raw: str) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        raise HTTPException(status_code=422, detail="symbol is required")

    match = _SYMBOL_RE.search(text)
    if not match:
        raise HTTPException(status_code=422, detail="invalid symbol, expected sh/sz + 6 digits or plain 6 digits")

    value = match.group(0).lower()
    if value.startswith(("sh", "sz")):
        return value

    if value.startswith(("6", "9")):
        return f"sh{value}"
    if value.startswith(("0", "3")):
        return f"sz{value}"
    raise HTTPException(status_code=422, detail="unable to infer market from symbol")


def load_watchlist() -> Dict:
    with _LOCK:
        data = _read_store_unlocked()
        normalized = _normalize_store(data)
        if data != normalized:
            _write_store_unlocked(normalized)
        return normalized


def upsert_item(symbol: str, name: str | None = None) -> Dict:
    normalized_symbol = normalize_symbol_input(symbol)
    normalized_name = _normalize_name(name)

    with _LOCK:
        data = _normalize_store(_read_store_unlocked())
        items = data["items"]
        index = next((i for i, item in enumerate(items) if item["symbol"] == normalized_symbol), -1)

        if index >= 0:
            if normalized_name:
                items[index]["name"] = normalized_name
        else:
            items.append({"symbol": normalized_symbol, "name": normalized_name})

        data["selected_symbol"] = normalized_symbol
        _write_store_unlocked(data)
        return data


def remove_item(symbol: str) -> Dict:
    normalized_symbol = normalize_symbol_input(symbol)

    with _LOCK:
        data = _normalize_store(_read_store_unlocked())
        items = data["items"]
        next_items = [item for item in items if item["symbol"] != normalized_symbol]
        if len(next_items) == len(items):
            raise HTTPException(status_code=404, detail="symbol not found in watchlist")
        if not next_items:
            raise HTTPException(status_code=409, detail="watchlist must keep at least one symbol")

        data["items"] = next_items
        if data["selected_symbol"] == normalized_symbol:
            data["selected_symbol"] = next_items[-1]["symbol"]
        _write_store_unlocked(data)
        return data


def select_item(symbol: str) -> Dict:
    normalized_symbol = normalize_symbol_input(symbol)

    with _LOCK:
        data = _normalize_store(_read_store_unlocked())
        symbols = {item["symbol"] for item in data["items"]}
        if normalized_symbol not in symbols:
            raise HTTPException(status_code=404, detail="symbol not found in watchlist")

        data["selected_symbol"] = normalized_symbol
        _write_store_unlocked(data)
        return data


def _normalize_store(data: Dict | None) -> Dict:
    payload = data if isinstance(data, dict) else {}
    raw_items = payload.get("items")
    items: List[Dict] = []

    if isinstance(raw_items, list):
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            symbol = item.get("symbol")
            try:
                normalized_symbol = normalize_symbol_input(str(symbol))
            except HTTPException:
                continue
            items.append(
                {
                    "symbol": normalized_symbol,
                    "name": _normalize_name(item.get("name")),
                }
            )

    if not items:
        items = [{"symbol": DEFAULT_SYMBOL, "name": None}]

    selected_symbol = payload.get("selected_symbol")
    try:
        normalized_selected = normalize_symbol_input(str(selected_symbol))
    except HTTPException:
        normalized_selected = items[0]["symbol"]

    if normalized_selected not in {item["symbol"] for item in items}:
        normalized_selected = items[0]["symbol"]

    return {
        "items": items,
        "selected_symbol": normalized_selected,
    }


def _normalize_name(name: str | None) -> str | None:
    if name is None:
        return None
    text = str(name).strip()
    return text or None


def _read_store_unlocked() -> Dict:
    if not _STORE_PATH.exists():
        data = _normalize_store({})
        _write_store_unlocked(data)
        return data

    try:
        content = _STORE_PATH.read_text(encoding="utf-8")
        data = json.loads(content) if content.strip() else {}
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


def _write_store_unlocked(data: Dict) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _STORE_PATH.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(_STORE_PATH)
