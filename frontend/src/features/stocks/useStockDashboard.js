import { computed, ref } from "vue";
import { showToast } from "vant";

import { DEFAULT_SYMBOL } from "../../constants/api";
import {
  fetchAnalyze,
  fetchDaily,
  fetchIntraday,
  fetchRealtime,
} from "../../services/api/stocks";

export function useStockDashboard() {
  const symbol = ref(DEFAULT_SYMBOL);
  const dailyData = ref([]);
  const intraday1Data = ref([]);
  const intraday5Data = ref([]);
  const realtime = ref(null);
  const aiResult = ref(null);

  const loadingDaily = ref(false);
  const loadingRealtime = ref(false);
  const loadingAI = ref(false);
  const loadingIntraday = ref(false);

  const latest = computed(() => {
    if (realtime.value?.price != null) {
      return {
        close: Number(realtime.value.price) || 0,
        open: Number(realtime.value.open) || 0,
        high: Number(realtime.value.high) || 0,
        low: Number(realtime.value.low) || 0,
        volume: Number(realtime.value.volume) || 0,
        change: 0,
        changePercent: "0.00",
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
      showToast(`日线加载失败: ${error.message}`);
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
      showToast(`实时行情加载失败: ${error.message}`);
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
      showToast(`AI 分析失败: ${error.message}`);
      aiResult.value = null;
    } finally {
      loadingAI.value = false;
    }
  }

  async function loadIntraday(period) {
    const key = String(period);
    const target = key === "5" ? intraday5Data : intraday1Data;
    if (target.value.length > 0) return;

    loadingIntraday.value = true;
    try {
      target.value = await fetchIntraday(symbol.value, key);
    } catch (error) {
      showToast(`${key}分钟图加载失败: ${error.message}`);
      target.value = [];
    } finally {
      loadingIntraday.value = false;
    }
  }

  async function loadAll() {
    await loadDaily();
    // Do not block first paint on realtime endpoint.
    loadRealtime();
  }

  return {
    symbol,
    dailyData,
    intraday1Data,
    intraday5Data,
    stockName,
    latest,
    aiResult,
    loadingDaily,
    loadingRealtime,
    loadingAI,
    loadingIntraday,
    loadAll,
    loadIntraday,
    analyzeTrend,
  };
}
