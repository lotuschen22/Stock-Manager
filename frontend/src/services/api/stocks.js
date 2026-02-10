import { request } from "./http";

export function fetchDaily(symbol) {
  return request(`/daily/${encodeURIComponent(symbol)}`);
}

export function fetchRealtime(symbol) {
  return request(`/realtime/${encodeURIComponent(symbol)}`);
}

export function fetchAnalyze(payload) {
  return request("/analyze", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchIntraday(symbol, period = "1") {
  const query = new URLSearchParams({ period: String(period) });
  return request(`/intraday/${encodeURIComponent(symbol)}?${query.toString()}`);
}
