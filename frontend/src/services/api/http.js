import { API_BASE_URL } from "../../constants/api";

export async function request(path, options = {}) {
  const url = `${API_BASE_URL}${path}`;
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = data?.detail || `Request failed: ${response.status}`;
    throw new Error(message);
  }
  return data;
}
