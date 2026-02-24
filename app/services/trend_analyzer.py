import json
import multiprocessing as mp
from typing import Dict, List

import requests

from app.config import (
    get_fallback_model,
    get_google_api_key,
    get_model_timeout_seconds,
    get_primary_model,
)

TREND_PROMPT = (
    "作为一名资深交易员，请基于给定的 OHLCV 序列，"
    "分析价格趋势和成交量变化，给出 100 字以内的短评，"
    "并给出一个 'bullish' (看多), 'bearish' (看空), 或 'neutral' (中性) 的标签。"
)


def analyze_stock_trend(data: List[Dict], timeframe: str = "day") -> Dict:
    """
    Return format:
    { "summary": "...", "signal": "bullish|bearish|neutral" }
    """
    if not data or len(data) < 5:
        return {
            "summary": "数据不足，无法生成趋势分析。",
            "signal": "neutral",
            "source": "local",
            "note": _reason_to_note("insufficient_data"),
            "model_used": "local",
        }

    api_key = get_google_api_key()
    if not api_key:
        return _local_trend_analysis(data, "missing_api_key")

    timeframe_desc = _timeframe_desc(timeframe)
    prompt = (
        f"{TREND_PROMPT}\n"
        f"当前分析周期: {timeframe_desc}。\n"
        f"样本条数: {len(data)}。\n"
        "Please return strict JSON only (no markdown).\n"
        "Format: {\"summary\":\"...\",\"signal\":\"bullish|bearish|neutral\"}\n"
        f"data: {json.dumps(data, ensure_ascii=False, default=str)}"
    )

    primary_model = get_primary_model()
    fallback_model = get_fallback_model()
    primary_timeout = 5
    fallback_timeout = get_model_timeout_seconds()
    model_used = primary_model

    try:
        text = _generate_model_text(prompt, primary_model, api_key, timeout_seconds=primary_timeout)
    except Exception as e:
        if _should_fallback(e):
            try:
                model_used = fallback_model
                text = _generate_model_text(
                    prompt,
                    fallback_model,
                    api_key,
                    timeout_seconds=fallback_timeout,
                )
            except Exception as fallback_error:
                return _local_trend_analysis(data, _safe_error_message(fallback_error), model_used="local")
        else:
            return _local_trend_analysis(data, _safe_error_message(e), model_used="local")

    if not text:
        return _local_trend_analysis(data, "empty_response", model_used="local")

    text = _strip_code_fence(text)

    try:
        payload = json.loads(text)
    except Exception:
        return _local_trend_analysis(data, "invalid_ai_payload", model_used="local")

    summary = str(payload.get("summary", "")).strip()[:100]
    signal = str(payload.get("signal", "neutral")).strip().lower()

    if signal not in {"bullish", "bearish", "neutral"}:
        signal = "neutral"
    if not summary:
        summary = "未生成有效分析结果。"

    return {
        "summary": summary,
        "signal": signal,
        "source": "ai",
        "note": "",
        "model_used": model_used,
    }


def _generate_model_text(
    prompt: str,
    model_name: str,
    api_key: str,
    timeout_seconds: int | float | None = None,
) -> str:
    timeout = float(timeout_seconds) if timeout_seconds is not None else float(get_model_timeout_seconds())
    timeout = max(1.0, timeout)
    queue: mp.Queue = mp.Queue(maxsize=1)
    ctx = mp.get_context("spawn")
    process = ctx.Process(
        target=_model_request_worker,
        args=(queue, prompt, model_name, api_key, timeout),
    )
    process.start()
    try:
        process.join(timeout=timeout + 1)
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)
            raise TimeoutError(f"model request timeout after {timeout}s")

        if queue.empty():
            raise RuntimeError("empty model response")

        ok, payload = queue.get()
        if not ok:
            raise RuntimeError(payload)
        return payload
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)


def _model_request_worker(
    queue: mp.Queue,
    prompt: str,
    model_name: str,
    api_key: str,
    timeout: int | float,
) -> None:
    try:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_name}:generateContent?key={api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        response = requests.post(url, json=payload, timeout=timeout)
        if response.status_code >= 400:
            detail = _extract_google_error(response.text)
            raise RuntimeError(detail)

        data = response.json()
        text = _extract_model_text(data)
        queue.put((True, text.strip()))
    except Exception as e:
        queue.put((False, str(e)))


def _should_fallback(error: Exception) -> bool:
    msg = str(error).lower()
    markers = (
        "timeout",
        "timed out",
        "deadline exceeded",
        "unavailable",
        "503",
        "resource exhausted",
        "rate limit",
        "quota",
    )
    return any(marker in msg for marker in markers)


def _safe_error_message(error: Exception) -> str:
    msg = str(error).strip()
    if not msg:
        return "ai_failed"
    lower_msg = msg.lower()
    if "timeout" in lower_msg or "timed out" in lower_msg or "deadline exceeded" in lower_msg:
        return "timeout"
    if "quota" in lower_msg or "resource exhausted" in lower_msg or "rate limit" in lower_msg:
        return "quota_limited"
    if "api key" in lower_msg or "permission" in lower_msg or "unauthorized" in lower_msg:
        return "auth_error"
    return "ai_failed"


def _local_trend_analysis(data: List[Dict], reason: str = "", model_used: str = "local") -> Dict:
    closes = [float(item.get("close", 0) or 0) for item in data]
    volumes = [float(item.get("volume", 0) or 0) for item in data]
    if len(closes) < 5:
        return {
            "summary": "数据不足，无法生成趋势分析。",
            "signal": "neutral",
            "source": "local",
            "note": _reason_to_note("insufficient_data"),
            "model_used": "local",
        }

    short_window = closes[-5:]
    long_window = closes[-20:] if len(closes) >= 20 else closes
    ma_short = sum(short_window) / len(short_window)
    ma_long = sum(long_window) / len(long_window)
    momentum = (closes[-1] - closes[-5]) / (closes[-5] or 1)

    signal = "neutral"
    if ma_short > ma_long and momentum > 0.01:
        signal = "bullish"
    elif ma_short < ma_long and momentum < -0.01:
        signal = "bearish"

    recent_vol = sum(volumes[-5:]) / max(1, min(5, len(volumes)))
    prev_slice = volumes[-10:-5] if len(volumes) >= 10 else volumes[:-5]
    prev_vol = (sum(prev_slice) / len(prev_slice)) if prev_slice else recent_vol
    vol_trend = "放量" if recent_vol > prev_vol else "平稳"
    ma_relation = "上方" if ma_short > ma_long else "下方"
    momentum_pct = momentum * 100
    direction = "上涨" if momentum_pct >= 0 else "回落"

    summary = (
        f"本地策略：MA5位于中期均线{ma_relation}，近5日{direction}{abs(momentum_pct):.2f}%，成交量{vol_trend}。"
    )
    return {
        "summary": summary[:100],
        "signal": signal,
        "source": "local",
        "note": _reason_to_note(reason),
        "model_used": model_used or "local",
    }


def _reason_to_note(reason: str) -> str:
    mapping = {
        "insufficient_data": "数据不足，已切换本地策略。",
        "missing_api_key": "AI服务未配置，已切换本地策略。",
        "timeout": "AI服务响应超时，已切换本地策略。",
        "quota_limited": "AI服务额度或频率受限，已切换本地策略。",
        "auth_error": "AI服务鉴权失败，已切换本地策略。",
        "invalid_ai_payload": "AI返回格式异常，已切换本地策略。",
        "empty_response": "AI返回为空，已切换本地策略。",
        "ai_failed": "AI服务暂不可用，已切换本地策略。",
    }
    return mapping.get(reason, mapping["ai_failed"])


def _strip_code_fence(text: str) -> str:
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if not lines:
        return text

    if lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    if lines and lines[0].strip().lower() == "json":
        lines = lines[1:]
    return "\n".join(lines).strip()


def _extract_google_error(text: str) -> str:
    try:
        payload = json.loads(text)
    except Exception:
        return "ai_failed"

    message = (
        payload.get("error", {}).get("message")
        or payload.get("error", {}).get("status")
        or "ai_failed"
    )
    return str(message)


def _extract_model_text(payload: Dict) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        raise RuntimeError("empty_response")

    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    if not parts:
        raise RuntimeError("empty_response")

    text = parts[0].get("text")
    if not text:
        raise RuntimeError("empty_response")
    return str(text)


def _timeframe_desc(timeframe: str) -> str:
    mapping = {
        "day": "日线",
        "time": "分时",
        "1m": "1分钟",
        "5m": "5分钟",
    }
    return mapping.get(timeframe, timeframe or "未知周期")
