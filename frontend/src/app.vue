<template>
  <van-config-provider theme="dark">
    <div class="app-container">
      <van-nav-bar
        :title="`${stockName} (${symbol})`"
        left-arrow
        fixed
        placeholder
        @click-left="showToast('返回上一页')"
      >
        <template #right>
          <van-icon name="search" size="18" />
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
          <div class="grid-item">
            <span>量</span> {{ (latest.volume / 10000).toFixed(0) }} 万
          </div>
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
            <p class="analysis-meta">模型：{{ formatModelUsed(aiResult.model_used) }}</p>
            <p v-if="aiResult.note" class="analysis-note">{{ aiResult.note }}</p>
          </div>
          <div v-else class="empty-state">
            点击下方按钮，调用后端 AI 分析接口。
          </div>
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
  </van-config-provider>
</template>

<script setup>
import { onMounted, onUnmounted, ref, watch } from "vue";
import * as echarts from "echarts";
import { showToast } from "vant";

import { useStockDashboard } from "./features/stocks/useStockDashboard";

const {
  symbol,
  stockName,
  dailyData,
  intraday1Data,
  intraday5Data,
  latest,
  aiResult,
  loadingAI,
  loadAll,
  loadIntraday,
  analyzeTrend,
} = useStockDashboard();

const chartRef = ref(null);
const activeTab = ref("time");
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

function renderChart() {
  if (!chartRef.value) return;
  if (!chart) chart = echarts.init(chartRef.value);
  if (activeTab.value === "time") {
    renderTimeChart();
    return;
  }
  if (activeTab.value === "1m") {
    renderMinuteKChart(intraday1Data.value, "MA10");
    return;
  }
  if (activeTab.value === "5m") {
    renderMinuteKChart(intraday5Data.value, "MA5");
    return;
  }
  renderDayKChart();
}

function renderDayKChart() {
  if (dailyData.value.length === 0) return;
  const dates = dailyData.value.map((i) => i.date);
  const values = dailyData.value.map((i) => [
    Number(i.open),
    Number(i.close),
    Number(i.low),
    Number(i.high),
  ]);

  chart.setOption(buildKlineOption(dates, values, calcMA(values, 5), "MA5"), true);
}

function renderMinuteKChart(data, maLabel) {
  if (!data || data.length === 0) return;
  const points = data
    .map((i) => {
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
      return { t: String(i.datetime).slice(11, 16), o: open, c: close, l: low, h: high };
    })
    .filter(Boolean);
  if (points.length === 0) return;

  const rawDates = points.map((i) => i.t);
  const rawValues = points.map((i) => [i.o, i.c, i.l, i.h]);
  const maCount = maLabel === "MA10" ? 10 : 5;
  const rawMa = calcMA(rawValues, maCount);

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

  chart.setOption(buildKlineOption(dates, values, maData, maLabel), true);
}

function renderTimeChart() {
  if (!intraday1Data.value || intraday1Data.value.length === 0) return;
  const points = intraday1Data.value
    .map((i) => ({ t: String(i.datetime).slice(11, 16), p: Number(i.close) }))
    .filter((i) => i.p > 0);
  if (points.length === 0) return;

  const dates = [];
  const prices = [];
  for (let i = 0; i < points.length; i += 1) {
    dates.push(points[i].t);
    prices.push(points[i].p);

    const next = points[i + 1];
    if (next && hasMiddayBreak(points[i].t, next.t)) {
      dates.push("休市");
      prices.push(null);
    }
  }
  const baseline = prices[0] || 0;

  chart.setOption(
    {
      backgroundColor: "transparent",
      grid: { left: "3%", right: "3%", bottom: "8%", top: "8%", containLabel: false },
      xAxis: {
        type: "category",
        data: dates,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: "#666", fontSize: 10, interval: 60 },
      },
      yAxis: {
        type: "value",
        scale: true,
        splitLine: { show: true, lineStyle: { color: "#333", width: 0.5 } },
        axisLabel: { color: "#666", fontSize: 10 },
      },
      series: [
        {
          type: "line",
          data: prices,
          smooth: true,
          connectNulls: false,
          symbol: "none",
          lineStyle: { width: 1.5, color: "#4da3ff" },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: "rgba(77,163,255,0.28)" },
              { offset: 1, color: "rgba(77,163,255,0.02)" },
            ]),
          },
          markLine: {
            silent: true,
            symbol: "none",
            lineStyle: { color: "#555", type: "dashed" },
            data: [{ yAxis: baseline }],
          },
        },
      ],
    },
    true,
  );
}

function buildKlineOption(dates, values, maData, maLabel) {
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
      data: dailyData.value.slice(-30),
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

watch([dailyData, intraday1Data, intraday5Data, activeTab], () => {
  renderChart();
});

onMounted(async () => {
  await Promise.allSettled([loadAll(), loadIntraday("1")]);
  renderChart();
  resizeHandler = () => chart?.resize();
  window.addEventListener("resize", resizeHandler);
});

onUnmounted(() => {
  if (resizeHandler) window.removeEventListener("resize", resizeHandler);
  if (chart) {
    chart.dispose();
    chart = null;
  }
});
</script>

<style scoped>
.app-container {
  min-height: 100vh;
  background-color: #121212;
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
  height: 300px;
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
  left: 0;
  width: 100%;
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
</style>
