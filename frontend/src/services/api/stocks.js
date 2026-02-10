import { request } from "./http";

export function fetchDaily(symbol) {
  return request(`/daily/${encodeURIComponent(symbol)}`);
}

export function fetchRealtime(symbol) {
  return request(`/realtime/${encodeURIComponent(symbol)}`);
}

export function fetchAnalyze(symbol) {
  return request(`/analyze/${encodeURIComponent(symbol)}`);
}
