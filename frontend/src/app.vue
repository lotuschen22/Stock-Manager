<template>
  <van-config-provider theme="dark">
    <div class="layout-root">
      <aside class="watch-sidebar">
        <button class="watch-search-trigger" type="button" @click="showAddPopup = true">
          <van-icon name="search" size="16" />
          <span>搜索即添加</span>
        </button>
        <div class="watch-list">
          <div v-if="loadingWatchlist" class="watch-empty">加载中...</div>
          <button
            v-for="item in watchlist"
            :key="item.symbol"
            type="button"
            class="watch-item"
            :class="{ active: symbol === item.symbol }"
            @click="handleSelectSymbol(item.symbol)"
          >
            <div class="watch-item-main">
              <div class="watch-item-name">{{ item.name || item.symbol }}</div>
              <div class="watch-item-code">{{ item.symbol }}</div>
            </div>
            <van-icon name="cross" class="watch-remove" @click.stop="handleRemoveSymbol(item.symbol)" />
          </button>
        </div>
      </aside>

      <div class="app-container">
        <van-nav-bar
          :title="`${stockName} (${symbol})`"
          left-arrow
          fixed
          placeholder
          @click-left="showToast('返回上一页')"
        >
          <template #right>
            <van-icon name="plus" size="18" @click="showAddPopup = true" />
          </template>
        </van-nav-bar>

        <div class="market-header">
          <div class="price-box" :class="getColor(latest.change)">
            <span class="current-price">{{ formatNumber(latest.close) }}</span>
            <div class="fluctuation">
              <span class="percent">{{ latest.changePercent }}%</span>
              <span class="amount">{{ formatChange(latest.change) }}</span>
            </div>
          </div>
          <div class="detail-grid">
            <div class="grid-item"><span>高</span> {{ formatNumber(latest.high) }}</div>
            <div class="grid-item"><span>开</span> {{ formatNumber(latest.open) }}</div>
            <div class="grid-item"><span>低</span> {{ formatNumber(latest.low) }}</div>
            <div class="grid-item"><span>量</span> {{ (latest.volume / 10000).toFixed(0) }} 万</div>
          </div>
        </div>

        <div class="chart-wrapper">
          <div class="chart-tabs">
            <span
              v-for="tab in chartTabs"
              :key="tab.key"
              :class="{ active: activeTab === tab.key }"
              @click="selectTab(tab.key)"
            >
              {{ tab.label }}
            </span>
          </div>
          <div ref="chartRef" class="echarts-container"></div>
        </div>

        <div class="ai-card">
          <div class="ai-header">
            <div class="ai-title">
              <span class="robot-icon">AI</span>
              <span>{{ aiResult?.source === "local" ? "本地策略分析" : "Gemini 趋势分析" }}</span>
            </div>
            <div v-if="aiResult" class="signal-tag" :class="aiResult.signal">
              {{ formatSignal(aiResult.signal) }}
            </div>
          </div>

          <div class="ai-body">
            <div v-if="loadingAI" class="loading-state">
              <van-loading type="spinner" color="#1989fa" size="24px" vertical>
                正在生成策略分析...
              </van-loading>
            </div>
            <div v-else-if="aiResult" class="analysis-content">
              <p>{{ aiResult.summary }}</p>
              <p class="analysis-meta">模型: {{ formatModelUsed(aiResult.model_used) }}</p>
              <p v-if="aiResult.note" class="analysis-note">{{ aiResult.note }}</p>
            </div>
            <div v-else class="empty-state">点击下方按钮，调用后端 AI 分析接口。</div>
          </div>
        </div>

        <div class="action-bar">
          <div class="action-left">
            <div class="action-icon"><van-icon name="star-o" /> 自选</div>
            <div class="action-icon"><van-icon name="bell" /> 预警</div>
          </div>
          <van-button
            round
            block
            class="ai-btn"
            color="linear-gradient(to right, #4f5d75, #2d3142)"
            :loading="loadingAI"
            @click="handleAnalyze"
          >
            生成 AI 分析
          </van-button>
        </div>
      </div>
    </div>

    <van-popup
      v-model:show="showAddPopup"
      round
      position="top"
      :style="{ margin: '12px', padding: '12px' }"
      closeable
      close-icon-position="top-right"
    >
      <div class="add-popup">
        <div class="add-title">添加股票</div>
        <van-field
          v-model="pendingInput"
          clearable
          placeholder="输入代码/名称/拼音，例如 600549、厦门钨业、xmw"
          @keyup.enter="handleAddSymbol"
        />
        <div v-if="searchingStocks" class="search-loading">搜索中...</div>
        <div v-else-if="searchResults.length > 0" class="search-list">
          <button
            v-for="item in searchResults"
            :key="item.symbol"
            type="button"
            class="search-item"
            @click="handlePickSearch(item)"
          >
            <span class="search-name">{{ item.name }}</span>
            <span class="search-code">{{ item.symbol }}</span>
          </button>
        </div>
        <div v-else-if="pendingInput.trim()" class="search-empty">无匹配结果，可直接按回车添加代码</div>
        <van-button block type="primary" :loading="addingSymbol" @click="handleAddSymbol">
          添加并切换
        </van-button>
      </div>
    </van-popup>
  </van-config-provider>
</template>

<script setup>
import { onMounted, onUnmounted, ref, watch } from "vue";
import * as echarts from "echarts";
import { showToast } from "vant";

import { useStockDashboard } from "./features/stocks/useStockDashboard";
import { searchStocks } from "./services/api/stocks";

const {
  symbol,
  watchlist,
  stockName,
  dailyData,
  intraday1Data,
  intraday5Data,
  intraday1Signals,
  intraday5Signals,
  latest,
  aiResult,
  loadingAI,
  loadingWatchlist,
  loadWatchlist,
  selectSymbol,
  addSymbol,
  removeSymbol,
  loadIntraday,
  analyzeTrend,
} = useStockDashboard();

const chartRef = ref(null);
const activeTab = ref("time");
const showAddPopup = ref(false);
const pendingInput = ref("");
const addingSymbol = ref(false);
const searchingStocks = ref(false);
const searchResults = ref([]);
let searchTimer = null;
const chartTabs = [
  { key: "time", label: "分时" },
  { key: "1m", label: "1分" },
  { key: "5m", label: "5分" },
  { key: "day", label: "日线" },
];
let chart = null;
let resizeHandler = null;

function getColor(value) {
  return value >= 0 ? "text-red" : "text-green";
}

function formatNumber(value) {
  return Number(value || 0).toFixed(2);
}

function formatChange(value) {
  const n = Number(value || 0);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}`;
}

function formatSignal(signal) {
  const map = {
    bullish: "看多",
    bearish: "看空",
    neutral: "中性",
  };
  return map[signal] || signal;
}

function formatModelUsed(modelUsed) {
  if (!modelUsed) return "未知";
  if (modelUsed === "local") return "本地策略";
  return modelUsed;
}

function calcMA(values, dayCount) {
  return values.map((_, i) => {
    if (i < dayCount) return "-";
    let sum = 0;
    for (let j = 0; j < dayCount; j += 1) {
      sum += values[i - j][1];
    }
    return (sum / dayCount).toFixed(2);
  });
}

function hasMiddayBreak(prevTime, nextTime) {
  return prevTime <= "11:30" && nextTime >= "13:00";
}

function getTimeCell(item) {
  return String(item.date || item.datetime || "");
}

function buildTradingTimeline() {
  const timeline = [];

  for (let hour = 9; hour <= 11; hour += 1) {
    const startMinute = hour === 9 ? 30 : 0;
    const endMinute = hour === 11 ? 30 : 59;
    for (let minute = startMinute; minute <= endMinute; minute += 1) {
      timeline.push(`${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`);
    }
  }

  timeline.push("11:30/13:00");

  for (let hour = 13; hour <= 15; hour += 1) {
    const endMinute = hour === 15 ? 0 : 59;
    for (let minute = 0; minute <= endMinute; minute += 1) {
      timeline.push(`${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`);
    }
  }

  return timeline;
}

function buildSignalMarks(signals, dateLabelMap, indexMap, options = {}) {
  const minGap = Number(options.minGap ?? 6);
  const symbolSize = Number(options.symbolSize ?? 6);
  const priceRangeMap = options.priceRangeMap instanceof Map ? options.priceRangeMap : null;
  const compacted = [];
  const sorted = (signals || [])
    .map((s) => {
      const rawDate = String(s?.date || "");
      const xLabel = dateLabelMap.get(rawDate);
      const idx = indexMap.get(rawDate);
      const price = Number(s?.price);
      const type = String(s?.type || "").toLowerCase();
      if (!xLabel || !Number.isFinite(idx) || !Number.isFinite(price)) return null;
      if (type !== "buy" && type !== "sell") return null;
      return { rawDate, xLabel, idx, price, type };
    })
    .filter(Boolean)
    .sort((a, b) => a.idx - b.idx);

  for (const item of sorted) {
    const prev = compacted[compacted.length - 1];
    if (!prev) {
      compacted.push(item);
      continue;
    }
    if (item.type !== prev.type || item.idx - prev.idx >= minGap) {
      compacted.push(item);
    }
  }

  return compacted.map((item) => {
    const isBuy = item.type === "buy";
    const color = isBuy ? "#6FD9AE" : "#F58FA0";
    const range = priceRangeMap?.get(item.rawDate);
    let y = item.price;
    if (range) {
      y = Math.min(range.max, Math.max(range.min, y));
    }
    return {
      coord: [item.xLabel, y],
      value: y,
      name: isBuy ? "BUY" : "SELL",
      symbol: "circle",
      symbolSize: Math.max(1, symbolSize),
      itemStyle: {
        color: "rgba(0,0,0,0)",
        borderWidth: 0,
        shadowBlur: 0,
      },
      label: {
        show: true,
        position: isBuy ? "bottom" : "top",
        distance: 0,
        formatter: isBuy ? "{line|│}\n{tag|B}" : "{tag|S}\n{line|│}",
        align: "center",
        color: "#fff",
        fontSize: 11,
        fontWeight: 700,
        lineHeight: 11,
        rich: {
          tag: {
            color: "#ffffff",
            backgroundColor: color,
            borderRadius: 10,
            padding: [2, 6],
            fontSize: 11,
            fontWeight: 700,
          },
          line: {
            color: color,
            fontSize: 11,
            lineHeight: 9,
          },
        },
      },
      tooltip: {
        formatter: `${isBuy ? "B" : "S"} ${y.toFixed(2)}`,
      },
    };
  });
}

function renderChart() {
  if (!chartRef.value) return;
  if (!chart) chart = echarts.init(chartRef.value);

  if (activeTab.value === "time") {
    renderTimeChart();
    return;
  }
  if (activeTab.value === "1m") {
    renderMinuteKChart(intraday1Data.value, intraday1Signals.value, "MA10");
    return;
  }
  if (activeTab.value === "5m") {
    renderMinuteKChart(intraday5Data.value, intraday5Signals.value, "MA5");
    return;
  }
  renderDayKChart();
}

function renderDayKChart() {
  if (!dailyData.value || dailyData.value.length === 0) return;
  const dates = dailyData.value.map((i) => String(i.date || ""));
  const values = dailyData.value.map((i) => [
    Number(i.open),
    Number(i.close),
    Number(i.low),
    Number(i.high),
  ]);
  chart.setOption(buildKlineOption(dates, values, calcMA(values, 5), "MA5"), true);
}

function renderMinuteKChart(data, signals, maLabel) {
  if (!data || data.length === 0) return;

  const points = data
    .map((i) => {
      const dt = getTimeCell(i);
      let open = Number(i.open);
      const close = Number(i.close);
      let high = Number(i.high);
      let low = Number(i.low);
      if (!(close > 0)) return null;
      if (!(open > 0)) open = close;
      if (!(high > 0)) high = Math.max(open, close);
      if (!(low > 0)) low = Math.min(open, close);
      high = Math.max(high, open, close, low);
      low = Math.min(low, open, close, high);
      return {
        rawDate: dt,
        t: dt.length >= 16 ? dt.slice(11, 16) : dt,
        o: open,
        c: close,
        l: low,
        h: high,
      };
    })
    .filter(Boolean);
  if (points.length === 0) return;

  const rawDates = points.map((i) => i.t);
  const rawValues = points.map((i) => [i.o, i.c, i.l, i.h]);
  const maCount = maLabel === "MA10" ? 10 : 5;
  const rawMa = calcMA(rawValues, maCount);

  const labelByDateMap = new Map();
  points.forEach((item) => {
    labelByDateMap.set(item.rawDate, item.t);
  });

  const pointIndexMap = new Map();
  const priceRangeMap = new Map();
  points.forEach((item, idx) => pointIndexMap.set(item.rawDate, idx));
  points.forEach((item) => priceRangeMap.set(item.rawDate, { min: item.l, max: item.h }));
  const markPoints = buildSignalMarks(signals, labelByDateMap, pointIndexMap, {
    minGap: 6,
    symbolSize: 6,
    priceRangeMap,
  });

  const dates = [];
  const values = [];
  const maData = [];
  for (let i = 0; i < rawDates.length; i += 1) {
    dates.push(rawDates[i]);
    values.push(rawValues[i]);
    maData.push(rawMa[i]);

    const nextTime = rawDates[i + 1];
    if (nextTime && hasMiddayBreak(rawDates[i], nextTime)) {
      dates.push("休市");
      values.push(["-", "-", "-", "-"]);
      maData.push("-");
    }
  }

  chart.setOption(buildKlineOption(dates, values, maData, maLabel, markPoints), true);
}

function renderTimeChart() {
  if (!intraday1Data.value || intraday1Data.value.length === 0) return;
  const points = intraday1Data.value
    .map((i) => {
      const rawDate = getTimeCell(i);
      return {
        rawDate,
        t: rawDate.slice(11, 16),
        p: Number(i.close),
        v: Number(i.volume) || 0,
      };
    })
    .filter((i) => i.t && i.p > 0);
  if (points.length === 0) return;

  const pointByTime = new Map();
  for (const item of points) {
    pointByTime.set(item.t, item);
  }

  const dates = buildTradingTimeline();
  const prices = [];
  const avgPrices = [];
  const volumes = [];
  const labelByDateMap = new Map();
  const pointIndexMap = new Map();
  const riseFlags = [];
  let volSum = 0;
  let amountSum = 0;
  let lastPrice = null;
  let dataIdx = -1;

  for (let i = 0; i < dates.length; i += 1) {
    const t = dates[i];
    if (t === "11:30/13:00") {
      prices.push(null);
      avgPrices.push(null);
      volumes.push(null);
      riseFlags.push(false);
      continue;
    }

    const item = pointByTime.get(t);
    if (!item) {
      prices.push(null);
      avgPrices.push(null);
      volumes.push(null);
      riseFlags.push(false);
      continue;
    }

    prices.push(item.p);
    volumes.push(item.v);
    volSum += Math.max(item.v, 0);
    amountSum += Math.max(item.p * item.v, 0);
    avgPrices.push(volSum > 0 ? amountSum / volSum : item.p);
    riseFlags.push(lastPrice == null ? true : item.p >= lastPrice);
    lastPrice = item.p;

    dataIdx += 1;
    labelByDateMap.set(item.rawDate, item.t);
    pointIndexMap.set(item.rawDate, dataIdx);
  }

  const rtRef = Number(latest.close) - Number(latest.change);
  const baseline = rtRef > 0 ? rtRef : prices.find((x) => Number.isFinite(x) && x > 0) || 0;
  const validPrices = prices.filter((x) => Number.isFinite(x));
  const maxDev =
    baseline > 0
      ? Math.max(...validPrices.map((v) => Math.abs(v - baseline)), baseline * 0.002)
      : Math.max(...validPrices, 1);
  const minY = Math.max(0, baseline - maxDev * 1.08);
  const maxY = baseline + maxDev * 1.08;
  const signalMarks = buildSignalMarks(intraday1Signals.value, labelByDateMap, pointIndexMap, {
    minGap: 8,
    symbolSize: 5,
  });

  chart.setOption(
    {
      backgroundColor: "transparent",
      grid: [
        { left: "4%", right: "6%", top: "8%", height: "63%" },
        { left: "4%", right: "6%", top: "76%", height: "15%" },
      ],
      xAxis: [
        {
          type: "category",
          boundaryGap: false,
          data: dates,
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { show: false },
          splitLine: { show: true, lineStyle: { color: "rgba(255,255,255,0.06)" } },
        },
        {
          type: "category",
          gridIndex: 1,
          boundaryGap: false,
          data: dates,
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: {
            color: "#7f8794",
            fontSize: 10,
            interval: 0,
            formatter: (value, idx) => {
              if (idx === 0) return "开盘 09:30";
              if (idx === dates.length - 1) return "收盘 15:00";
              if (value === "11:30/13:00") return value;
              return "";
            },
          },
          splitLine: { show: false },
        },
      ],
      yAxis: [
        {
          type: "value",
          min: minY,
          max: maxY,
          scale: true,
          splitNumber: 4,
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { show: true, lineStyle: { color: "rgba(255,255,255,0.08)", width: 0.8 } },
          axisLabel: { color: "#9da7b5", fontSize: 10, formatter: (v) => Number(v).toFixed(2) },
        },
        {
          type: "value",
          min: minY,
          max: maxY,
          scale: true,
          splitNumber: 4,
          position: "right",
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: {
            color: "#9da7b5",
            fontSize: 10,
            formatter: (v) => {
              if (!(baseline > 0)) return "0.00%";
              const pct = ((Number(v) - baseline) / baseline) * 100;
              return `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
            },
          },
        },
        {
          type: "value",
          gridIndex: 1,
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { show: false },
          splitLine: { show: false },
        },
      ],
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
      },
      series: [
        {
          name: "分时",
          type: "line",
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: prices,
          smooth: false,
          connectNulls: false,
          symbol: "none",
          lineStyle: { width: 1.6, color: "#3f9cff" },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: "rgba(63,156,255,0.30)" },
              { offset: 1, color: "rgba(63,156,255,0.03)" },
            ]),
          },
          markLine: {
            silent: true,
            symbol: "none",
            lineStyle: { color: "rgba(230, 107, 107, 0.55)", type: "dashed", width: 1 },
            data: [{ yAxis: baseline }],
          },
          markPoint: {
            symbolKeepAspect: true,
            data: signalMarks,
          },
        },
        {
          name: "均价",
          type: "line",
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: avgPrices,
          smooth: true,
          connectNulls: false,
          symbol: "none",
          lineStyle: { width: 1.2, color: "#e0a53a", opacity: 0.9 },
        },
        {
          name: "成交量",
          type: "bar",
          xAxisIndex: 1,
          yAxisIndex: 2,
          data: volumes,
          barWidth: "55%",
          itemStyle: {
            color: (params) =>
              riseFlags[Number(params.dataIndex)] ? "rgba(253,16,80,0.62)" : "rgba(12,244,155,0.62)",
          },
        },
      ],
    },
    true,
  );
}

function buildKlineOption(dates, values, maData, maLabel, markPoints = []) {
  return {
    backgroundColor: "transparent",
    grid: { left: "2%", right: "2%", bottom: "2%", top: "10%", containLabel: false },
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { show: false },
    },
    yAxis: {
      scale: true,
      splitLine: { show: true, lineStyle: { color: "#333", width: 0.5 } },
      axisLabel: { inside: true, color: "#666", fontSize: 10 },
    },
    series: [
      {
        name: "K",
        type: "candlestick",
        data: values,
        markPoint: {
          symbolKeepAspect: true,
          data: markPoints,
        },
        itemStyle: {
          color: "#FD1050",
          color0: "#0CF49B",
          borderColor: "#FD1050",
          borderColor0: "#0CF49B",
        },
      },
      {
        name: maLabel,
        type: "line",
        data: maData,
        smooth: true,
        symbol: "none",
        lineStyle: { opacity: 0.55, color: "#fff" },
      },
    ],
  };
}

async function selectTab(tabKey) {
  activeTab.value = tabKey;
  if (tabKey === "time" || tabKey === "1m") {
    await loadIntraday("1");
  } else if (tabKey === "5m") {
    await loadIntraday("5");
  }
  renderChart();
}

function currentAnalyzePayload() {
  if (activeTab.value === "day") {
    return {
      symbol: symbol.value,
      timeframe: "day",
      data: dailyData.value.slice(-60),
    };
  }
  if (activeTab.value === "5m") {
    return {
      symbol: symbol.value,
      timeframe: "5m",
      data: intraday5Data.value.slice(-180),
    };
  }
  if (activeTab.value === "1m") {
    return {
      symbol: symbol.value,
      timeframe: "1m",
      data: intraday1Data.value.slice(-180),
    };
  }
  return {
    symbol: symbol.value,
    timeframe: "time",
    data: intraday1Data.value.slice(-180),
  };
}

async function handleAnalyze() {
  const payload = currentAnalyzePayload();
  if (!payload.data || payload.data.length < 5) {
    showToast("当前图表数据不足，无法分析");
    return;
  }
  await analyzeTrend(payload);
}

async function handleSelectSymbol(nextSymbol) {
  if (nextSymbol === symbol.value) return;
  await selectSymbol(nextSymbol);
}

async function handleRemoveSymbol(nextSymbol) {
  await removeSymbol(nextSymbol);
}

async function handleAddSymbol() {
  const text = pendingInput.value.trim();
  if (!text) {
    showToast("请输入股票代码");
    return;
  }

  addingSymbol.value = true;
  try {
    const ok = await addSymbol(text);
    if (ok) {
      pendingInput.value = "";
      showAddPopup.value = false;
    }
  } finally {
    addingSymbol.value = false;
  }
}

async function executeSearch(keyword) {
  const q = String(keyword || "").trim();
  if (!q) {
    searchResults.value = [];
    return;
  }
  searchingStocks.value = true;
  try {
    const payload = await searchStocks(q, 12);
    searchResults.value = Array.isArray(payload?.items) ? payload.items : [];
  } catch (error) {
    searchResults.value = [];
  } finally {
    searchingStocks.value = false;
  }
}

async function handlePickSearch(item) {
  if (!item?.symbol) return;
  addingSymbol.value = true;
  try {
    const ok = await addSymbol(item);
    if (ok) {
      pendingInput.value = "";
      searchResults.value = [];
      showAddPopup.value = false;
    }
  } finally {
    addingSymbol.value = false;
  }
}

watch([dailyData, intraday1Data, intraday5Data, intraday1Signals, intraday5Signals, activeTab], () => {
  renderChart();
});

watch(pendingInput, (value) => {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    executeSearch(value);
  }, 220);
});

watch(showAddPopup, (visible) => {
  if (!visible) {
    pendingInput.value = "";
    searchResults.value = [];
    searchingStocks.value = false;
  }
});

onMounted(async () => {
  await loadWatchlist();
  renderChart();
  resizeHandler = () => chart?.resize();
  window.addEventListener("resize", resizeHandler);
});

onUnmounted(() => {
  if (searchTimer) clearTimeout(searchTimer);
  if (resizeHandler) window.removeEventListener("resize", resizeHandler);
  if (chart) {
    chart.dispose();
    chart = null;
  }
});
</script>

<style scoped>
.layout-root {
  min-height: 100vh;
  display: flex;
  background: #121212;
}

.watch-sidebar {
  width: 220px;
  border-right: 1px solid #262626;
  background: linear-gradient(180deg, #171717 0%, #111111 100%);
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.watch-search-trigger {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid #373737;
  background: #1d1d1d;
  color: #b7c0cd;
  border-radius: 10px;
  padding: 10px 12px;
  cursor: pointer;
}

.watch-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  overflow: auto;
}

.watch-empty {
  color: #767e89;
  font-size: 12px;
  padding: 12px;
}

.watch-item {
  width: 100%;
  text-align: left;
  border: 1px solid transparent;
  background: #171b22;
  color: #d7dce4;
  border-radius: 10px;
  padding: 10px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.watch-item.active {
  border-color: #3f9cff;
  background: rgba(63, 156, 255, 0.12);
}

.watch-item-name {
  font-size: 13px;
  font-weight: 600;
}

.watch-item-code {
  margin-top: 3px;
  font-size: 11px;
  color: #8e99a8;
}

.watch-remove {
  opacity: 0;
  color: #8a93a1;
  transition: opacity 0.18s;
}

.watch-item:hover .watch-remove,
.watch-item.active .watch-remove {
  opacity: 1;
}

.app-container {
  flex: 1;
  min-height: 100vh;
  color: #fff;
  font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
  padding-bottom: 80px;
}

.market-header {
  padding: 16px 20px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.current-price {
  font-size: 36px;
  font-weight: 700;
  line-height: 1;
}

.fluctuation {
  display: flex;
  gap: 10px;
  margin-top: 6px;
  font-size: 14px;
  font-weight: 500;
}

.text-red {
  color: #fd1050;
}

.text-green {
  color: #0cf49b;
}

.detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px 12px;
  font-size: 12px;
  color: #888;
}

.grid-item span {
  color: #555;
  margin-right: 4px;
}

.chart-wrapper {
  margin-top: 10px;
  border-bottom: 1px solid #222;
}

.chart-tabs {
  display: flex;
  padding: 0 16px;
  gap: 24px;
  font-size: 14px;
  color: #666;
  margin-bottom: 10px;
  user-select: none;
}

.chart-tabs span {
  cursor: pointer;
}

.chart-tabs .active {
  color: #fff;
  font-weight: bold;
  position: relative;
}

.chart-tabs .active::after {
  content: "";
  position: absolute;
  bottom: -4px;
  left: 0;
  width: 100%;
  height: 2px;
  background: #fff;
}

.echarts-container {
  width: 100%;
  height: 360px;
}

.ai-card {
  margin: 20px 16px;
  background: #1e1e1e;
  border-radius: 16px;
  padding: 20px;
  border: 1px solid #333;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
}

.ai-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.ai-title {
  font-size: 16px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 8px;
  color: #e0e0e0;
}

.robot-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  font-size: 11px;
  border-radius: 50%;
  background: #2d3142;
}

.signal-tag {
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 700;
}

.signal-tag.bullish {
  background: rgba(253, 16, 80, 0.15);
  color: #fd1050;
  border: 1px solid rgba(253, 16, 80, 0.3);
}

.signal-tag.bearish {
  background: rgba(12, 244, 155, 0.15);
  color: #0cf49b;
  border: 1px solid rgba(12, 244, 155, 0.3);
}

.signal-tag.neutral {
  background: rgba(255, 255, 255, 0.12);
  color: #ddd;
  border: 1px solid rgba(255, 255, 255, 0.22);
}

.ai-body {
  min-height: 80px;
  background: #161616;
  border-radius: 12px;
  padding: 16px;
  font-size: 14px;
  line-height: 1.6;
  color: #ccc;
}

.analysis-note {
  margin-top: 8px;
  color: #888;
  font-size: 12px;
}

.analysis-meta {
  margin-top: 8px;
  color: #9aa4b2;
  font-size: 12px;
}

.loading-state {
  padding: 20px 0;
  text-align: center;
}

.empty-state {
  color: #555;
  text-align: center;
  padding-top: 20px;
}

.action-bar {
  position: fixed;
  bottom: 0;
  left: 220px;
  right: 0;
  background: #1e1e1e;
  padding: 10px 16px 20px;
  display: flex;
  align-items: center;
  gap: 16px;
  box-shadow: 0 -4px 12px rgba(0, 0, 0, 0.2);
  z-index: 99;
}

.action-left {
  display: flex;
  gap: 20px;
  color: #888;
  font-size: 10px;
  text-align: center;
}

.action-icon {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.ai-btn {
  flex: 1;
  font-weight: bold;
  letter-spacing: 1px;
}

.add-popup {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.add-title {
  font-size: 15px;
  font-weight: 700;
  color: #d8dee8;
}

.search-loading,
.search-empty {
  font-size: 12px;
  color: #8b95a3;
  padding: 4px 2px;
}

.search-list {
  max-height: 260px;
  overflow: auto;
  border: 1px solid #2e3540;
  border-radius: 10px;
  background: #121821;
}

.search-item {
  width: 100%;
  border: 0;
  background: transparent;
  color: #dbe2ed;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  cursor: pointer;
}

.search-item + .search-item {
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.search-item:hover {
  background: rgba(63, 156, 255, 0.14);
}

.search-name {
  font-size: 13px;
  font-weight: 600;
}

.search-code {
  font-size: 12px;
  color: #97a3b3;
}

@media (max-width: 900px) {
  .watch-sidebar {
    width: 132px;
    padding: 10px 8px;
  }

  .action-bar {
    left: 132px;
  }

  .watch-search-trigger span {
    display: none;
  }
}
</style>
