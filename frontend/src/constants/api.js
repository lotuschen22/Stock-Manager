export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

export const DEFAULT_SYMBOL = "sh600549";

export function getWsBaseUrl() {
  const raw = import.meta.env.VITE_WS_BASE_URL || API_BASE_URL;
  if (raw.startsWith("https://")) return raw.replace("https://", "wss://");
  if (raw.startsWith("http://")) return raw.replace("http://", "ws://");
  return raw;
}
