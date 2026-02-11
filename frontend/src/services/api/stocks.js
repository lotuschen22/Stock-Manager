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

export function fetchKline(code, period = "daily") {
  const query = new URLSearchParams({
    code: String(code),
    period: String(period),
  });
  return request(`/api/kline?${query.toString()}`);
}

export function fetchWatchlist() {
  return request("/watchlist");
}

export function addWatchlistItem(payload) {
  return request("/watchlist/items", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deleteWatchlistItem(symbol) {
  return request(`/watchlist/items/${encodeURIComponent(symbol)}`, {
    method: "DELETE",
  });
}

export function updateSelectedSymbol(symbol) {
  return request("/watchlist/selected", {
    method: "PUT",
    body: JSON.stringify({ symbol }),
  });
}

export function searchStocks(query, limit = 12) {
  const q = String(query || "").trim();
  if (!q) return Promise.resolve({ items: [] });
  const params = new URLSearchParams({
    q,
    limit: String(limit),
  });
  return request(`/stocks/search?${params.toString()}`);
}
