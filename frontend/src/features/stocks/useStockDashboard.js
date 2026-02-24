import { computed, ref } from "vue";
import { showToast } from "vant";

import { DEFAULT_SYMBOL, getWsBaseUrl } from "../../constants/api";
import {
  addWatchlistItem,
  deleteWatchlistItem,
  fetchAnalyze,
  fetchDaily,
  fetchSignals,
  fetchTimeline,
  fetchIntraday,
  fetchKline,
  fetchRealtime,
  fetchWatchlist,
  updateSelectedSymbol,
} from "../../services/api/stocks";

export function useStockDashboard() {
  const ALERT_COOLDOWN_MS = 3 * 60 * 1000;
  const CACHE_TTL_MS = {
    realtime: 3000,
    daily: 120000,
    intraday1: 20000,
    intraday5: 45000,
  };

  const symbol = ref(DEFAULT_SYMBOL);
  const watchlist = ref([]);
  const dailyData = ref([]);
  const intraday1Data = ref([]);
  const timelineData = ref([]);
  const intraday5Data = ref([]);
  const intraday1Signals = ref([]);
  const intraday5Signals = ref([]);
  const realtime = ref(null);
  const aiResult = ref(null);

  const marketBySymbol = ref({});
  const marketServerTime = ref("");
  const marketPhase = ref("closed");
  const signalAlerts = ref([]);
  const wsConnected = ref(false);
  const alertMuted = ref(false);

  const loadingDaily = ref(false);
  const loadingRealtime = ref(false);
  const loadingAI = ref(false);
  const loadingIntraday = ref(false);
  const loadingWatchlist = ref(false);
  const requestVersion = ref(0);
  const realtimeCache = new Map();
  const dailyCache = new Map();
  const intradayCache = new Map();
  const timelineCache = new Map();
  const signalCache = new Map();
  const lastSignalBySymbol = new Map();
  const lastAlertAt = new Map();

  let marketSocket = null;
  let reconnectTimer = null;
  let shouldReconnect = true;

  const latest = computed(() => {
    if (realtime.value?.price != null) {
      const rtPrice = Number(realtime.value.price) || 0;
      const rtOpen = Number(realtime.value.open) || 0;
      const rtChange = Number(realtime.value.change);
      const rtChangePercent = Number(realtime.value.change_percent);

      if (Number.isFinite(rtChange) && Number.isFinite(rtChangePercent)) {
        return {
          close: rtPrice,
          open: rtOpen,
          high: Number(realtime.value.high) || 0,
          low: Number(realtime.value.low) || 0,
          volume: Number(realtime.value.volume) || 0,
          change: rtChange,
          changePercent: rtChangePercent.toFixed(2),
        };
      }

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
    const selected = watchlist.value.find(
      (item) => String(item?.symbol || "").toLowerCase() === symbol.value,
    );
    const localName = String(selected?.name || "").trim();
    if (localName) {
      return localName;
    }
    const name = realtime.value?.name;
    if (typeof name === "string" && name.trim()) {
      return name.trim();
    }
    return symbol.value;
  });

  function isLatestRequest(version) {
    return version == null || version === requestVersion.value;
  }

  function readCache(cache, key, ttlMs) {
    const item = cache.get(key);
    if (!item) return null;
    if (Date.now() - item.at > ttlMs) {
      cache.delete(key);
      return null;
    }
    return item.value;
  }

  function writeCache(cache, key, value) {
    cache.set(key, {
      at: Date.now(),
      value,
    });
  }

  function buildMarketWsUrl() {
    return `${getWsBaseUrl().replace(/\/$/, "")}/ws/market`;
  }

  function resetReconnectTimer() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function ensureMarketSocket() {
    if (typeof window === "undefined") return;
    if (
      marketSocket &&
      (marketSocket.readyState === WebSocket.OPEN || marketSocket.readyState === WebSocket.CONNECTING)
    ) {
      if (marketSocket.readyState === WebSocket.OPEN) {
        syncMarketSubscription();
      }
      return;
    }

    resetReconnectTimer();
    const ws = new WebSocket(buildMarketWsUrl());
    marketSocket = ws;

    ws.onopen = () => {
      wsConnected.value = true;
      syncMarketSubscription();
    };

    ws.onmessage = (event) => {
      let payload = null;
      try {
        payload = JSON.parse(event.data);
      } catch (_error) {
        return;
      }
      handleMarketMessage(payload);
    };

    ws.onclose = () => {
      wsConnected.value = false;
      if (!shouldReconnect) return;
      resetReconnectTimer();
      reconnectTimer = setTimeout(() => {
        ensureMarketSocket();
      }, 1500);
    };

    ws.onerror = () => {
      wsConnected.value = false;
    };
  }

  function disconnectMarketSocket() {
    shouldReconnect = false;
    resetReconnectTimer();
    wsConnected.value = false;
    if (marketSocket) {
      marketSocket.close();
      marketSocket = null;
    }
  }

  function syncMarketSubscription() {
    if (!marketSocket || marketSocket.readyState !== WebSocket.OPEN) return;
    const symbols = watchlist.value.map((item) => normalizeSymbol(item.symbol)).filter(Boolean);
    marketSocket.send(
      JSON.stringify({
        type: "subscribe",
        symbols,
        timeframe: "1",
        focus_symbol: symbol.value,
      }),
    );
  }

  function handleMarketMessage(payload) {
    if (!payload || typeof payload !== "object") return;
    if (String(payload.type || "").toLowerCase() !== "batch") return;
    marketServerTime.value = payload?.ts ? String(payload.ts) : marketServerTime.value;
    marketPhase.value = payload?.market_phase ? String(payload.market_phase) : marketPhase.value;

    const items = Array.isArray(payload.items) ? payload.items : [];
    if (items.length === 0) return;

    const next = { ...marketBySymbol.value };
    for (const item of items) {
      const normalized = normalizeSymbol(item?.symbol);
      if (!normalized) continue;
      if (String(item?.type || "").toLowerCase() === "error") {
        const prev = next[normalized] || {};
        next[normalized] = {
          ...prev,
          price: NaN,
          changePercent: NaN,
          updatedAt: null,
          unavailable: true,
          error: String(item?.detail || ""),
        };
        if (normalized === symbol.value) {
          realtime.value = null;
        }
        continue;
      }

      const signal = normalizeSignal(item?.signal);
      const signalTs = item?.signal_ts ? String(item.signal_ts) : null;
      const price = toFiniteNumber(item?.price);

      next[normalized] = {
        price,
        changePercent: toFiniteNumber(item?.change_percent),
        signal,
        signalTs,
        updatedAt: item?.updated_at ? String(item.updated_at) : null,
        unavailable: false,
        error: "",
      };

      if (normalized === symbol.value && Number.isFinite(price)) {
        const current = realtime.value && typeof realtime.value === "object" ? realtime.value : {};
        realtime.value = {
          ...current,
          symbol: normalized,
          price,
          prev_close: toNullableFinite(item?.prev_close),
          change: toNullableFinite(item?.change),
          change_percent: toFiniteNumber(item?.change_percent),
          open: toNullableFinite(item?.open),
          high: toNullableFinite(item?.high),
          low: toNullableFinite(item?.low),
          volume: toNullableFinite(item?.volume),
          time: item?.updated_at ? String(item.updated_at) : current?.time || null,
        };
      }

      if (signal && signalTs && Number.isFinite(price)) {
        const signalRecord = {
          date: signalTs,
          type: signal === "B" ? "buy" : "sell",
          price,
        };
        if (normalized === symbol.value) {
          intraday1Signals.value = mergeRealtimeSignal(intraday1Signals.value, signalRecord);
        }
        const cacheKey = `${normalized}:1`;
        const cached = intradayCache.get(cacheKey);
        if (cached?.value && Array.isArray(cached.value.signals)) {
          const mergedSignals = mergeRealtimeSignal(cached.value.signals, signalRecord);
          writeCache(intradayCache, cacheKey, {
            data: Array.isArray(cached.value.data) ? cached.value.data : [],
            signals: mergedSignals,
          });
        }
      }

      maybeTriggerSignalAlert(normalized, signal, signalTs, price);
    }

    marketBySymbol.value = next;
  }

  function maybeTriggerSignalAlert(symbolKey, signal, signalTs, price) {
    const prevSignal = lastSignalBySymbol.get(symbolKey) || null;
    if (!signal) {
      if (prevSignal) lastSignalBySymbol.set(symbolKey, null);
      return;
    }

    if (signal !== prevSignal) {
      lastSignalBySymbol.set(symbolKey, signal);
    } else {
      return;
    }

    const cooldownKey = `${symbolKey}:1:${signal}`;
    const now = Date.now();
    const lastAt = lastAlertAt.get(cooldownKey) || 0;
    if (now - lastAt < ALERT_COOLDOWN_MS) return;

    lastAlertAt.set(cooldownKey, now);
    const watchItem = watchlist.value.find((item) => normalizeSymbol(item.symbol) === symbolKey);
    const displayName = watchItem?.name || symbolKey;

    signalAlerts.value = [
      ...signalAlerts.value,
      {
        id: `${symbolKey}-${signal}-${now}`,
        symbol: symbolKey,
        name: displayName,
        signal,
        signalTs,
        price,
        createdAt: now,
      },
    ].slice(-20);
  }

  function consumeSignalAlert(alertId) {
    signalAlerts.value = signalAlerts.value.filter((item) => item.id !== alertId);
  }

  function setAlertMuted(value) {
    alertMuted.value = Boolean(value);
  }

  function toggleAlertMuted() {
    alertMuted.value = !alertMuted.value;
    return alertMuted.value;
  }

  async function hydrateWatchlistQuotes() {
    const symbols = watchlist.value.map((item) => normalizeSymbol(item.symbol)).filter(Boolean);
    if (symbols.length === 0) return;

    const tasks = symbols.map(async (symbolKey) => {
      try {
        const data = await fetchRealtime(symbolKey);
        const price = toFiniteNumber(data?.price);
        const open = toFiniteNumber(data?.open);
        const changePercentRaw = Number(data?.change_percent);
        const changePercent = Number.isFinite(changePercentRaw)
          ? changePercentRaw
          : (open > 0 ? ((price - open) / open) * 100 : 0);
        return {
          symbol: symbolKey,
          price,
          changePercent,
          signal: null,
          signalTs: null,
          updatedAt: data?.time ? String(data.time) : null,
        };
      } catch (_error) {
        return null;
      }
    });

    const settled = await Promise.all(tasks);
    const next = { ...marketBySymbol.value };
    for (const item of settled) {
      if (!item) continue;
      const prev = next[item.symbol] || {};
      next[item.symbol] = {
        ...prev,
        ...item,
      };
    }
    marketBySymbol.value = next;
  }

  async function loadDaily(version) {
    const reqVersion = version ?? requestVersion.value;
    const currentSymbol = symbol.value;
    const cached = readCache(dailyCache, currentSymbol, CACHE_TTL_MS.daily);
    if (cached) {
      if (isLatestRequest(reqVersion)) dailyData.value = cached;
      return;
    }
    loadingDaily.value = true;
    try {
      const data = await fetchDaily(currentSymbol);
      if (!isLatestRequest(reqVersion)) return;
      dailyData.value = data;
      writeCache(dailyCache, currentSymbol, data);
    } catch (error) {
      if (!isLatestRequest(reqVersion)) return;
      showToast(`Failed to load daily data: ${error.message}`);
      dailyData.value = [];
    } finally {
      if (isLatestRequest(reqVersion)) loadingDaily.value = false;
    }
  }

  async function loadRealtime(version) {
    const reqVersion = version ?? requestVersion.value;
    const currentSymbol = symbol.value;
    const cached = readCache(realtimeCache, currentSymbol, CACHE_TTL_MS.realtime);
    if (cached) {
      if (isLatestRequest(reqVersion)) realtime.value = cached;
      return;
    }
    loadingRealtime.value = true;
    try {
      const data = await fetchRealtime(currentSymbol);
      if (!isLatestRequest(reqVersion)) return;
      realtime.value = data;
      writeCache(realtimeCache, currentSymbol, data);
    } catch (error) {
      if (!isLatestRequest(reqVersion)) return;
      showToast(`Failed to load realtime data: ${error.message}`);
      realtime.value = null;
    } finally {
      if (isLatestRequest(reqVersion)) loadingRealtime.value = false;
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

  async function loadIntraday(period, version, forceRefresh = false) {
    const reqVersion = version ?? requestVersion.value;
    const key = String(period);
    const currentSymbol = symbol.value;
    const cacheKey = `${currentSymbol}:${key}`;
    const targetData = key === "5" ? intraday5Data : intraday1Data;
    const targetSignals = key === "5" ? intraday5Signals : intraday1Signals;

    if (!forceRefresh) {
      const cached = readCache(
        intradayCache,
        cacheKey,
        key === "5" ? CACHE_TTL_MS.intraday5 : CACHE_TTL_MS.intraday1,
      );
      if (cached) {
        if (isLatestRequest(reqVersion)) {
          targetData.value = cached.data;
          targetSignals.value = cached.signals;
        }
        return;
      }
    }

    loadingIntraday.value = true;
    try {
      let payload = null;
      let usedFallback = false;
      try {
        payload = await fetchKline(currentSymbol, key);
      } catch (_error) {
        usedFallback = true;
      }

      let allData = Array.isArray(payload?.data) ? payload.data : [];
      let allSignals = Array.isArray(payload?.signals) ? payload.signals : [];

      if (allData.length === 0) {
        const fallbackRows = await fetchIntraday(currentSymbol, key);
        allData = Array.isArray(fallbackRows) ? fallbackRows : [];
        allSignals = [];
        usedFallback = true;
      }

      if (!isLatestRequest(reqVersion)) return;

      const latestDay = getLatestTradingDay(allData);
      const dayData = latestDay
        ? allData.filter((item) => String(item?.date || "").startsWith(latestDay))
        : allData;
      const daySignals = latestDay
        ? allSignals.filter((item) => String(item?.date || "").startsWith(latestDay))
        : allSignals;

      targetData.value = dayData;
      targetSignals.value = daySignals;
      writeCache(intradayCache, cacheKey, {
        data: dayData,
        signals: daySignals,
      });
      if (usedFallback) {
        showToast(`${key}m data loaded via fallback source`);
      }
    } catch (error) {
      if (!isLatestRequest(reqVersion)) return;
      showToast(`Failed to load ${key}m data: ${shortErrorMessage(error)}`);
      targetData.value = [];
      targetSignals.value = [];
    } finally {
      if (isLatestRequest(reqVersion)) loadingIntraday.value = false;
    }
  }

  async function loadTimeline(version, forceRefresh = false) {
    const reqVersion = version ?? requestVersion.value;
    const currentSymbol = symbol.value;
    const cacheKey = `${currentSymbol}:time`;

    if (!forceRefresh) {
      const cached = readCache(timelineCache, cacheKey, 3000);
      if (cached && Array.isArray(cached.items)) {
        if (isLatestRequest(reqVersion)) timelineData.value = cached.items;
        return;
      }
    }

    try {
      const payload = await fetchTimeline(currentSymbol);
      if (!isLatestRequest(reqVersion)) return;
      const items = Array.isArray(payload?.items) ? payload.items : [];
      timelineData.value = items;
      writeCache(timelineCache, cacheKey, { items });
    } catch (_error) {
      if (!isLatestRequest(reqVersion)) return;
      timelineData.value = [];
    }
  }

  async function loadSignalOnly(period, version, forceRefresh = false) {
    const reqVersion = version ?? requestVersion.value;
    const key = String(period);
    if (key !== "1" && key !== "5") return;
    const currentSymbol = symbol.value;
    const cacheKey = `${currentSymbol}:${key}`;
    const targetSignals = key === "5" ? intraday5Signals : intraday1Signals;

    if (!forceRefresh) {
      const cached = readCache(signalCache, cacheKey, 20000);
      if (cached && Array.isArray(cached.signals)) {
        if (isLatestRequest(reqVersion)) targetSignals.value = cached.signals;
        return;
      }
    }

    try {
      const payload = await fetchSignals(currentSymbol, key);
      if (!isLatestRequest(reqVersion)) return;
      const allSignals = Array.isArray(payload?.signals) ? payload.signals : [];
      targetSignals.value = allSignals;
      writeCache(signalCache, cacheKey, { signals: allSignals });
    } catch (_error) {
      // Signal refresh is non-critical; keep existing points.
    }
  }

  function clearChartData() {
    dailyData.value = [];
    timelineData.value = [];
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
    syncMarketSubscription();
  }

  async function refreshForSymbol(nextSymbol, preloadTab = "time") {
    const normalized = normalizeSymbol(nextSymbol);
    if (!normalized) return;
    requestVersion.value += 1;
    const version = requestVersion.value;
    symbol.value = normalized;
    syncMarketSubscription();
    clearChartData();

    if (preloadTab === "day") {
      await Promise.allSettled([loadRealtime(version), loadDaily(version)]);
      void loadSignalOnly("1", version, true);
      void loadSignalOnly("5", version, true);
      return;
    }
    if (preloadTab === "5m") {
      await Promise.allSettled([loadRealtime(version), loadIntraday("5", version)]);
      void loadSignalOnly("1", version, true);
      return;
    }
    if (preloadTab === "1m") {
      await Promise.allSettled([loadRealtime(version), loadIntraday("1", version)]);
      void loadSignalOnly("5", version, true);
      return;
    }
    // "time" uses dedicated timeline endpoint.
    await Promise.allSettled([loadRealtime(version), loadTimeline(version, true)]);
    void loadSignalOnly("1", version, true);
    void loadSignalOnly("5", version, true);
  }

  async function loadWatchlist(preloadTab = "time") {
    loadingWatchlist.value = true;
    shouldReconnect = true;
    ensureMarketSocket();

    try {
      const payload = await fetchWatchlist();
      applyWatchlistState(payload);
      // Do not block first paint on slow market data providers.
      void refreshForSymbol(symbol.value, preloadTab);
      void hydrateWatchlistQuotes();
    } catch (error) {
      showToast(`Failed to load watchlist: ${error.message}`);
      watchlist.value = [{ symbol: DEFAULT_SYMBOL, name: "" }];
      syncMarketSubscription();
      void refreshForSymbol(DEFAULT_SYMBOL, preloadTab);
      void hydrateWatchlistQuotes();
    } finally {
      loadingWatchlist.value = false;
    }
  }

  async function selectSymbol(nextSymbol, preloadTab = "time") {
    const normalized = normalizeSymbol(nextSymbol);
    if (!normalized) return;
    if (normalized === symbol.value) return;
    try {
      // Refresh first for responsiveness, persist selection in background.
      await refreshForSymbol(normalized, preloadTab);
      await hydrateWatchlistQuotes();
      void updateSelectedSymbol(normalized).catch((error) => {
        showToast(`Failed to persist selected symbol: ${error.message}`);
      });
    } catch (error) {
      showToast(`Failed to switch symbol: ${error.message}`);
    }
  }

  async function addSymbol(rawInput, preloadTab = "time") {
    const parsed = normalizeInputToPayload(rawInput);
    if (!parsed.symbol) {
      showToast("请输入有效代码，如 600549 或 sh600549");
      return false;
    }

    try {
      const payload = await addWatchlistItem(parsed);
      applyWatchlistState(payload);
      await refreshForSymbol(symbol.value, preloadTab);
      await hydrateWatchlistQuotes();
      return true;
    } catch (error) {
      showToast(`Failed to add symbol: ${error.message}`);
      return false;
    }
  }

  async function removeSymbol(nextSymbol, preloadTab = "time") {
    const normalized = normalizeSymbol(nextSymbol);
    if (!normalized) return;
    try {
      const payload = await deleteWatchlistItem(normalized);
      const prevSelected = symbol.value;
      applyWatchlistState(payload);
      if (prevSelected !== symbol.value) {
        await refreshForSymbol(symbol.value, preloadTab);
      }
      await hydrateWatchlistQuotes();
    } catch (error) {
      showToast(`Failed to remove symbol: ${error.message}`);
    }
  }

  return {
    symbol,
    watchlist,
    dailyData,
    intraday1Data,
    timelineData,
    intraday5Data,
    intraday1Signals,
    intraday5Signals,
    stockName,
    latest,
    aiResult,
    marketBySymbol,
    marketServerTime,
    marketPhase,
    signalAlerts,
    wsConnected,
    alertMuted,
    loadingDaily,
    loadingRealtime,
    loadingAI,
    loadingIntraday,
    loadingWatchlist,
    loadWatchlist,
    selectSymbol,
    addSymbol,
    removeSymbol,
    loadDaily,
    loadIntraday,
    loadTimeline,
    loadSignalOnly,
    analyzeTrend,
    consumeSignalAlert,
    setAlertMuted,
    toggleAlertMuted,
    disconnectMarketSocket,
  };
}

function getLatestTradingDay(rows) {
  if (!Array.isArray(rows) || rows.length === 0) return "";
  const last = rows[rows.length - 1];
  const value = String(last?.date || last?.datetime || "");
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

function normalizeSignal(raw) {
  const text = String(raw || "").trim().toUpperCase();
  if (text === "B" || text === "S") return text;
  return null;
}

function toFiniteNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function toNullableFinite(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function mergeRealtimeSignal(existingSignals, incoming) {
  const list = Array.isArray(existingSignals) ? [...existingSignals] : [];
  const targetDate = String(incoming?.date || "");
  const targetType = String(incoming?.type || "").toLowerCase();
  if (!targetDate || (targetType !== "buy" && targetType !== "sell")) return list;

  const idx = list.findIndex((item) => {
    const d = String(item?.date || "");
    const t = String(item?.type || "").toLowerCase();
    return d === targetDate && t === targetType;
  });
  if (idx >= 0) {
    list[idx] = {
      ...list[idx],
      ...incoming,
    };
    return list;
  }
  list.push(incoming);
  list.sort((a, b) => String(a?.date || "").localeCompare(String(b?.date || "")));
  return list;
}
