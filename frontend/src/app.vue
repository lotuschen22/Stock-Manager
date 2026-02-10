<template>
  <van-config-provider theme="dark">
    <div class="app-container">
      <van-nav-bar
        :title="`股票看盘 (${symbol})`"
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
          <span class="active">日线</span>
          <span>周线</span>
          <span>月线</span>
        </div>
        <div ref="chartRef" class="echarts-container"></div>
      </div>

      <div class="ai-card">
        <div class="ai-header">
          <div class="ai-title">
            <span class="robot-icon">AI</span>
            <span>Gemini 趋势分析</span>
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
          @click="analyzeTrend"
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
  dailyData,
  latest,
  aiResult,
  loadingAI,
  loadAll,
  analyzeTrend,
} = useStockDashboard();

const chartRef = ref(null);
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

function renderChart() {
  if (!chartRef.value || dailyData.value.length === 0) return;
  if (!chart) chart = echarts.init(chartRef.value);

  const dates = dailyData.value.map((i) => i.date);
  const values = dailyData.value.map((i) => [
    Number(i.open),
    Number(i.close),
    Number(i.low),
    Number(i.high),
  ]);

  chart.setOption({
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
        type: "line",
        data: calcMA(values, 5),
        smooth: true,
        lineStyle: { opacity: 0.5, color: "#fff" },
      },
    ],
  });
}

watch(dailyData, () => {
  renderChart();
});

onMounted(async () => {
  await loadAll();
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
