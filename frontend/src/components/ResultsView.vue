<template>
  <div v-if="result" class="results-view">
    <el-divider content-position="left">🎯 核心数据概览</el-divider>
    <el-row :gutter="16">
      <el-col :span="6"><MetricCard label="总请求发送数" :value="result.total_requests" /></el-col>
      <el-col :span="6">
        <MetricCard label="成功请求数" :value="result.successful_requests"
          :delta="successRate" deltaColor="success" />
      </el-col>
      <el-col :span="6"><MetricCard label="总共耗时 (秒)" :value="totalTime" /></el-col>
      <el-col :span="6"><MetricCard label="累计输出 Token 总数" :value="result.total_output_tokens" /></el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 12px">
      <el-col :span="8"><MetricCard label="RPS (QPS)" :value="rps" /></el-col>
      <el-col :span="8"><MetricCard label="TPS 平均" :value="tpsAvg" /></el-col>
      <el-col :span="8"><MetricCard label="并发度" :value="result.concurrency" /></el-col>
    </el-row>

    <el-divider content-position="left">📈 细粒度分位数统计指标</el-divider>
    <el-row :gutter="16">
      <el-col :span="8">
        <el-table :data="latencyTable" size="small" border>
          <el-table-column prop="name" label="指标分位数" />
          <el-table-column prop="value" label="响应延迟 (秒)" />
        </el-table>
      </el-col>
      <el-col :span="8">
        <el-table :data="ttftTable" size="small" border>
          <el-table-column prop="name" label="指标分位数" />
          <el-table-column prop="value" label="TTFT (秒)" />
        </el-table>
      </el-col>
      <el-col :span="8">
        <el-table :data="tpsTable" size="small" border>
          <el-table-column prop="name" label="指标分位数" />
          <el-table-column prop="value" label="TPS (Tokens/秒)" />
        </el-table>
      </el-col>
    </el-row>

    <el-divider content-position="left">📊 直观分布图表</el-divider>
    <el-row :gutter="16">
      <el-col :span="12">
        <div ref="latencyChart" style="height: 300px"></div>
      </el-col>
      <el-col :span="12">
        <div ref="tpsChart" style="height: 300px"></div>
      </el-col>
    </el-row>

    <el-divider content-position="left">📂 原始数据</el-divider>
    <el-collapse>
      <el-collapse-item title="查看原始压测结果 JSON 数据">
        <pre style="background:#f5f7fa;padding:12px;border-radius:4px;overflow:auto">{{ JSON.stringify(result, null, 2) }}</pre>
      </el-collapse-item>
    </el-collapse>

    <el-divider content-position="left">📥 导出结果</el-divider>
    <el-button type="primary" @click="$emit('export', 'excel')">导出 Excel</el-button>
    <el-button @click="$emit('export', 'csv')">导出 CSV</el-button>
  </div>
</template>

<script>
import echarts from 'echarts'
import MetricCard from './MetricCard.vue'

export default {
  name: 'ResultsView',
  components: { MetricCard },
  props: { result: { type: Object, default: null } },
  data() {
    return { charts: [] }
  },
  watch: {
    result: {
      immediate: true,
      handler() {
        this.$nextTick(() => this.renderCharts())
      }
    }
  },
  beforeDestroy() {
    this.charts.forEach(c => c && c.dispose())
  },
  computed: {
    successRate() {
      if (!this.result || !this.result.total_requests) return ''
      return ((this.result.successful_requests / this.result.total_requests) * 100).toFixed(1) + '% 成功率'
    },
    totalTime() { return this.result ? this.result.total_time.toFixed(2) : '' },
    rps() { return this.result ? this.result.requests_per_second.toFixed(2) : '' },
    tpsAvg() { return this.result ? this.result.tokens_per_second.average.toFixed(2) : '' },
    latencyTable() {
      if (!this.result) return []
      const l = this.result.latency
      const fmt = v => v != null ? v.toFixed(3) : '-'
      return [
        { name: '平均值', value: fmt(l.average) },
        { name: 'P50', value: fmt(l.p50) },
        { name: 'P95', value: fmt(l.p95) },
        { name: 'P99', value: fmt(l.p99) }
      ]
    },
    ttftTable() {
      if (!this.result) return []
      const t = this.result.time_to_first_token
      const fmt = v => v != null ? v.toFixed(3) : '-'
      return [
        { name: '平均值', value: fmt(t.average) },
        { name: 'P50', value: fmt(t.p50) },
        { name: 'P95', value: fmt(t.p95) },
        { name: 'P99', value: fmt(t.p99) }
      ]
    },
    tpsTable() {
      if (!this.result) return []
      const t = this.result.tokens_per_second
      const fmt = v => v != null ? v.toFixed(2) : '-'
      return [
        { name: '平均值', value: fmt(t.average) },
        { name: 'P50', value: fmt(t.p50) },
        { name: 'P95', value: fmt(t.p95) },
        { name: 'P99', value: fmt(t.p99) }
      ]
    }
  },
  methods: {
    renderCharts() {
      this.charts.forEach(c => c && c.dispose())
      this.charts = []
      if (!this.result) return

      if (this.$refs.latencyChart) {
        const chart = echarts.init(this.$refs.latencyChart)
        chart.setOption({
          title: { text: '响应延迟分布情况 (秒)', left: 'center' },
          tooltip: {},
          xAxis: { type: 'category', data: ['平均值', 'P50', 'P95', 'P99'] },
          yAxis: { type: 'value' },
          series: [{
            type: 'bar',
            data: [this.result.latency.average, this.result.latency.p50, this.result.latency.p95, this.result.latency.p99].map(v => v != null ? v : 0),
            itemStyle: { color: '#1f77b4' }
          }]
        })
        this.charts.push(chart)
      }

      if (this.$refs.tpsChart) {
        const chart = echarts.init(this.$refs.tpsChart)
        chart.setOption({
          title: { text: 'TPS 分布情况 (Tokens/秒)', left: 'center' },
          tooltip: {},
          xAxis: { type: 'category', data: ['平均值', 'P50', 'P95', 'P99'] },
          yAxis: { type: 'value' },
          series: [{
            type: 'bar',
            data: [this.result.tokens_per_second.average, this.result.tokens_per_second.p50, this.result.tokens_per_second.p95, this.result.tokens_per_second.p99].map(v => v != null ? v : 0),
            itemStyle: { color: '#ff7f0e' }
          }]
        })
        this.charts.push(chart)
      }
    }
  }
}
</script>

<style scoped>
.results-view { margin-top: 16px; }
</style>