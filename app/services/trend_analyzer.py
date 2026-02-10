import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Dict, List

import google.generativeai as genai

from app.config import (
    get_fallback_model,
    get_google_api_key,
    get_model_timeout_seconds,
    get_primary_model,
)

TREND_PROMPT = (
    "\u4f5c\u4e3a\u4e00\u540d\u8d44\u6df1\u4ea4\u6613\u5458\uff0c\u6839\u636e\u6700\u8fd1 30 \u5929\u7684 OHLCV \u6570\u636e\uff0c"
    "\u5206\u6790\u4ef7\u683c\u8d8b\u52bf\u548c\u6210\u4ea4\u91cf\u53d8\u5316\uff0c\u7ed9\u51fa 100 \u5b57\u4ee5\u5185\u7684\u77ed\u8bc4\uff0c"
    "\u5e76\u7ed9\u51fa\u4e00\u4e2a 'bullish' (\u770b\u591a), 'bearish' (\u770b\u7a7a), \u6216 'neutral' (\u4e2d\u6027) \u7684\u6807\u7b7e\u3002"
)


def analyze_stock_trend(data: List[Dict]) -> Dict:
    """
    Return format:
    { "summary": "...", "signal": "bullish|bearish|neutral" }
    """
    if not data or len(data) < 5:
        return {"summary": "Not enough data to analyze trend.", "signal": "neutral"}

    api_key = get_google_api_key()
    if not api_key:
        return {"summary": "Missing GOOGLE_API_KEY.", "signal": "neutral"}

    prompt = (
        f"{TREND_PROMPT}\n"
        "Please return strict JSON only (no markdown).\n"
        "Format: {\"summary\":\"...\",\"signal\":\"bullish|bearish|neutral\"}\n"
        f"data: {json.dumps(data, ensure_ascii=False, default=str)}"
    )

    try:
        genai.configure(api_key=api_key)
        text = _generate_model_text(prompt, get_primary_model())
    except Exception as e:
        if _should_fallback(e):
            try:
                text = _generate_model_text(prompt, get_fallback_model())
            except Exception:
                return {"summary": "AI request failed.", "signal": "neutral"}
        else:
            return {"summary": "AI request failed.", "signal": "neutral"}

    if not text:
        return {"summary": "AI request failed.", "signal": "neutral"}

    text = _strip_code_fence(text)

    try:
        payload = json.loads(text)
    except Exception:
        return {"summary": text[:100] if text else "Invalid AI response.", "signal": "neutral"}

    summary = str(payload.get("summary", "")).strip()[:100]
    signal = str(payload.get("signal", "neutral")).strip().lower()

    if signal not in {"bullish", "bearish", "neutral"}:
        signal = "neutral"
    if not summary:
        summary = "No effective analysis result."

    return {"summary": summary, "signal": signal}


def _generate_model_text(prompt: str, model_name: str) -> str:
    timeout = get_model_timeout_seconds()

    def _run_request() -> str:
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(
            prompt, request_options={"timeout": timeout}
        )
        return (response.text or "").strip()

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_run_request)
    try:
        return future.result(timeout=timeout)
    except FutureTimeoutError as exc:
        future.cancel()
        raise TimeoutError(f"model request timeout after {timeout}s") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _should_fallback(error: Exception) -> bool:
    msg = str(error).lower()
    markers = ("timeout", "timed out", "deadline exceeded", "unavailable", "503")
    return any(marker in msg for marker in markers)


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
