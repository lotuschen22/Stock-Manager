import { computed, ref } from "vue";
import { showToast } from "vant";

import { DEFAULT_SYMBOL } from "../../constants/api";
import {
  addWatchlistItem,
  deleteWatchlistItem,
  fetchAnalyze,
  fetchDaily,
  fetchKline,
  fetchRealtime,
  fetchWatchlist,
  updateSelectedSymbol,
} from "../../services/api/stocks";

export function useStockDashboard() {
  const symbol = ref(DEFAULT_SYMBOL);
  const watchlist = ref([]);
  const dailyData = ref([]);
  const intraday1Data = ref([]);
  const intraday5Data = ref([]);
  const intraday1Signals = ref([]);
  const intraday5Signals = ref([]);
  const realtime = ref(null);
  const aiResult = ref(null);

  const loadingDaily = ref(false);
  const loadingRealtime = ref(false);
  const loadingAI = ref(false);
  const loadingIntraday = ref(false);
  const loadingWatchlist = ref(false);

  const latest = computed(() => {
    if (realtime.value?.price != null) {
      const rtPrice = Number(realtime.value.price) || 0;
      const rtOpen = Number(realtime.value.open) || 0;
      let refClose = 0;

      if (dailyData.value.length >= 2) {
        const last = dailyData.value[dailyData.value.length - 1];
        const prev = dailyData.value[dailyData.value.length - 2];
        const lastClose = Number(last.close) || 0;
        const prevClose = Number(prev.close) || 0;
        const lastOpen = Number(last.open) || 0;
        const sameSession =
          rtOpen > 0 &&
          lastOpen > 0 &&
          Math.abs(lastOpen - rtOpen) <= Math.max(0.02, rtOpen * 0.002);

        refClose = sameSession && prevClose > 0 ? prevClose : lastClose;
        if (refClose <= 0 && prevClose > 0) refClose = prevClose;
      } else if (dailyData.value.length === 1) {
        refClose = Number(dailyData.value[0].close) || 0;
      }

      if (refClose <= 0 && rtOpen > 0) refClose = rtOpen;
      const change = refClose > 0 ? rtPrice - refClose : 0;
      const changePercent = refClose > 0 ? ((change / refClose) * 100).toFixed(2) : "0.00";

      return {
        close: rtPrice,
        open: rtOpen,
        high: Number(realtime.value.high) || 0,
        low: Number(realtime.value.low) || 0,
        volume: Number(realtime.value.volume) || 0,
        change,
        changePercent,
      };
    }

    if (dailyData.value.length < 2) {
      return {
        close: 0,
        open: 0,
        high: 0,
        low: 0,
        volume: 0,
        change: 0,
        changePercent: "0.00",
      };
    }

    const last = dailyData.value[dailyData.value.length - 1];
    const prev = dailyData.value[dailyData.value.length - 2];
    const change = Number(last.close) - Number(prev.close);
    const base = Number(prev.close) || 1;

    return {
      close: Number(last.close) || 0,
      open: Number(last.open) || 0,
      high: Number(last.high) || 0,
      low: Number(last.low) || 0,
      volume: Number(last.volume) || 0,
      change,
      changePercent: ((change / base) * 100).toFixed(2),
    };
  });

  const stockName = computed(() => {
    const name = realtime.value?.name;
    if (typeof name === "string" && name.trim()) {
      return name.trim();
    }
    return symbol.value;
  });

  async function loadDaily() {
    loadingDaily.value = true;
    try {
      dailyData.value = await fetchDaily(symbol.value);
    } catch (error) {
      showToast(`Failed to load daily data: ${error.message}`);
      dailyData.value = [];
    } finally {
      loadingDaily.value = false;
    }
  }

  async function loadRealtime() {
    loadingRealtime.value = true;
    try {
      realtime.value = await fetchRealtime(symbol.value);
    } catch (error) {
      showToast(`Failed to load realtime data: ${error.message}`);
      realtime.value = null;
    } finally {
      loadingRealtime.value = false;
    }
  }

  async function analyzeTrend(payload) {
    loadingAI.value = true;
    aiResult.value = null;
    try {
      aiResult.value = await fetchAnalyze(payload);
    } catch (error) {
      showToast(`AI analyze failed: ${error.message}`);
      aiResult.value = null;
    } finally {
      loadingAI.value = false;
    }
  }

  async function loadIntraday(period) {
    const key = String(period);
    const targetData = key === "5" ? intraday5Data : intraday1Data;
    const targetSignals = key === "5" ? intraday5Signals : intraday1Signals;
    if (targetData.value.length > 0) return;

    loadingIntraday.value = true;
    try {
      const payload = await fetchKline(symbol.value, key);
      const allData = Array.isArray(payload?.data) ? payload.data : [];
      const allSignals = Array.isArray(payload?.signals) ? payload.signals : [];

      const latestDay = getLatestTradingDay(allData);
      const dayData = latestDay
        ? allData.filter((item) => String(item?.date || "").startsWith(latestDay))
        : allData;
      const daySignals = latestDay
        ? allSignals.filter((item) => String(item?.date || "").startsWith(latestDay))
        : allSignals;

      targetData.value = dayData;
      targetSignals.value = daySignals;
    } catch (error) {
      showToast(`Failed to load ${key}m data: ${shortErrorMessage(error)}`);
      targetData.value = [];
      targetSignals.value = [];
    } finally {
      loadingIntraday.value = false;
    }
  }

  async function loadAll() {
    await loadDaily();
    loadRealtime();
  }

  function clearChartData() {
    dailyData.value = [];
    intraday1Data.value = [];
    intraday5Data.value = [];
    intraday1Signals.value = [];
    intraday5Signals.value = [];
    realtime.value = null;
    aiResult.value = null;
  }

  function applyWatchlistState(payload) {
    const items = Array.isArray(payload?.items) ? payload.items : [];
    watchlist.value = items.map((item) => ({
      symbol: String(item?.symbol || "").toLowerCase(),
      name: item?.name ? String(item.name).trim() : "",
    }));

    const selected = String(payload?.selected_symbol || "").toLowerCase();
    const fallback = watchlist.value[0]?.symbol || DEFAULT_SYMBOL;
    symbol.value = selected || fallback;
  }

  async function refreshForSymbol(nextSymbol) {
    const normalized = normalizeSymbol(nextSymbol);
    if (!normalized) return;
    symbol.value = normalized;
    clearChartData();
    await Promise.allSettled([loadAll(), loadIntraday("1")]);
  }

  async function loadWatchlist() {
    loadingWatchlist.value = true;
    try {
      const payload = await fetchWatchlist();
      applyWatchlistState(payload);
      await refreshForSymbol(symbol.value);
    } catch (error) {
      showToast(`Failed to load watchlist: ${error.message}`);
      watchlist.value = [{ symbol: DEFAULT_SYMBOL, name: "" }];
      await refreshForSymbol(DEFAULT_SYMBOL);
    } finally {
      loadingWatchlist.value = false;
    }
  }

  async function selectSymbol(nextSymbol) {
    const normalized = normalizeSymbol(nextSymbol);
    if (!normalized) return;
    try {
      const payload = await updateSelectedSymbol(normalized);
      applyWatchlistState(payload);
      await refreshForSymbol(symbol.value);
    } catch (error) {
      showToast(`Failed to switch symbol: ${error.message}`);
    }
  }

  async function addSymbol(rawInput) {
    const parsed = normalizeInputToPayload(rawInput);
    if (!parsed.symbol) {
      showToast("请输入有效代码，如 600549 或 sh600549");
      return false;
    }

    try {
      const payload = await addWatchlistItem(parsed);
      applyWatchlistState(payload);
      await refreshForSymbol(symbol.value);
      return true;
    } catch (error) {
      showToast(`Failed to add symbol: ${error.message}`);
      return false;
    }
  }

  async function removeSymbol(nextSymbol) {
    const normalized = normalizeSymbol(nextSymbol);
    if (!normalized) return;
    try {
      const payload = await deleteWatchlistItem(normalized);
      const prevSelected = symbol.value;
      applyWatchlistState(payload);
      if (prevSelected !== symbol.value) {
        await refreshForSymbol(symbol.value);
      }
    } catch (error) {
      showToast(`Failed to remove symbol: ${error.message}`);
    }
  }

  return {
    symbol,
    watchlist,
    dailyData,
    intraday1Data,
    intraday5Data,
    intraday1Signals,
    intraday5Signals,
    stockName,
    latest,
    aiResult,
    loadingDaily,
    loadingRealtime,
    loadingAI,
    loadingIntraday,
    loadingWatchlist,
    loadWatchlist,
    selectSymbol,
    addSymbol,
    removeSymbol,
    loadIntraday,
    analyzeTrend,
  };
}

function getLatestTradingDay(rows) {
  if (!Array.isArray(rows) || rows.length === 0) return "";
  const last = rows[rows.length - 1];
  const value = String(last?.date || "");
  return value.length >= 10 ? value.slice(0, 10) : "";
}

function shortErrorMessage(error) {
  const text = String(error?.message || error || "").trim();
  if (!text) return "unknown error";
  return text.length > 120 ? `${text.slice(0, 120)}...` : text;
}

function parseSymbolInput(raw) {
  const text = String(raw || "").trim();
  const match = text.match(/(sh|sz)?\d{6}/i);
  if (!match) return { symbol: "", name: "" };

  const symbol = normalizeSymbol(match[0]);
  const name = text
    .replace(match[0], "")
    .replace(/[-_/|]/g, " ")
    .trim();
  return { symbol, name: name || undefined };
}

function normalizeSymbol(raw) {
  const text = String(raw || "").toLowerCase().trim();
  const match = text.match(/(sh|sz)?\d{6}/);
  if (!match) return "";
  const value = match[0];
  if (value.startsWith("sh") || value.startsWith("sz")) return value;
  if (value.startsWith("6") || value.startsWith("9")) return `sh${value}`;
  if (value.startsWith("0") || value.startsWith("3")) return `sz${value}`;
  return "";
}

function normalizeInputToPayload(raw) {
  if (raw && typeof raw === "object") {
    const symbol = normalizeSymbol(raw.symbol);
    const name = String(raw.name || "").trim();
    return { symbol, name: name || undefined };
  }
  return parseSymbolInput(raw);
}


