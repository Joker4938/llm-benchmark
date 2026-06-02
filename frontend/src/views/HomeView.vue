<template>
  <div class="home-view">
    <el-row :gutter="20">
      <el-col :span="6">
        <ConfigPanel ref="configPanel" :running="running" @start="startBenchmark" />
      </el-col>
      <el-col :span="18">
        <ProgressBar :visible="running" :percentage="progressPercent" :status="progressStatus" :text="progressText" />

        <div v-if="gradientResults.length > 0">
          <el-divider content-position="left">📊 自动化梯度压测性能报告</el-divider>
          <el-table :data="gradientTable" size="small" border stripe>
            <el-table-column prop="concurrency" label="并发数" />
            <el-table-column prop="successRate" label="成功率 (%)" />
            <el-table-column prop="rps" label="RPS" />
            <el-table-column prop="tps" label="TPS" />
            <el-table-column prop="avgLatency" label="平均延迟 (秒)" />
            <el-table-column prop="p99Latency" label="P99 延迟 (秒)" />
            <el-table-column prop="ttft" label="首字延迟 (秒)" />
          </el-table>
          <el-row :gutter="16" style="margin-top: 16px">
            <el-col :span="12"><div ref="gradientRpsChart" style="height: 300px"></div></el-col>
            <el-col :span="12"><div ref="gradientLatencyChart" style="height: 300px"></div></el-col>
          </el-row>
          <el-button type="primary" style="margin-top: 12px" @click="exportGradient">导出梯度报告</el-button>
        </div>

        <ResultsView v-if="singleResult" :result="singleResult" @export="exportSingle" />
      </el-col>
    </el-row>
  </div>
</template>

<script>
import echarts from 'echarts'
import ConfigPanel from '@/components/ConfigPanel.vue'
import ProgressBar from '@/components/ProgressBar.vue'
import ResultsView from '@/components/ResultsView.vue'
import api from '@/api'

export default {
  name: 'HomeView',
  components: { ConfigPanel, ProgressBar, ResultsView },
  data() {
    return {
      running: false,
      progressPercent: 0,
      progressStatus: '',
      progressText: '',
      singleResult: null,
      gradientResults: [],
      reportFiles: {},
      pollTimer: null,
      charts: []
    }
  },
  computed: {
    gradientTable() {
      const fmt = (v, d) => v != null ? v.toFixed(d) : '-'
      return this.gradientResults.map(r => ({
        concurrency: r.concurrency,
        successRate: ((r.successful_requests / r.total_requests) * 100).toFixed(1),
        rps: fmt(r.requests_per_second, 2),
        tps: fmt(r.tokens_per_second.average, 2),
        avgLatency: fmt(r.latency.average, 3),
        p99Latency: fmt(r.latency.p99, 3),
        ttft: fmt(r.time_to_first_token.average, 3)
      }))
    }
  },
  watch: {
    gradientResults: {
      immediate: true,
      handler() {
        this.$nextTick(() => this.renderCharts())
      }
    }
  },
  beforeDestroy() {
    this.stopPolling()
    this.charts.forEach(c => c && c.dispose())
  },
  methods: {
    renderCharts() {
      this.charts.forEach(c => c && c.dispose())
      this.charts = []
      if (this.gradientResults.length === 0) return

      if (this.$refs.gradientRpsChart) {
        const chart = echarts.init(this.$refs.gradientRpsChart)
        chart.setOption({
          title: { text: 'RPS 随并发变化趋势', left: 'center' },
          tooltip: { trigger: 'axis' },
          xAxis: { type: 'category', data: this.gradientResults.map(r => r.concurrency), name: '并发数' },
          yAxis: { type: 'value', name: 'RPS' },
          series: [{ type: 'line', data: this.gradientResults.map(r => r.requests_per_second != null ? r.requests_per_second.toFixed(2) : 0), smooth: true, itemStyle: { color: '#2ca02c' } }]
        })
        this.charts.push(chart)
      }

      if (this.$refs.gradientLatencyChart) {
        const chart = echarts.init(this.$refs.gradientLatencyChart)
        chart.setOption({
          title: { text: '平均延迟随并发变化趋势', left: 'center' },
          tooltip: { trigger: 'axis' },
          xAxis: { type: 'category', data: this.gradientResults.map(r => r.concurrency), name: '并发数' },
          yAxis: { type: 'value', name: '延迟 (秒)' },
          series: [{ type: 'line', data: this.gradientResults.map(r => r.latency.average != null ? r.latency.average.toFixed(3) : 0), smooth: true, itemStyle: { color: '#d62728' } }]
        })
        this.charts.push(chart)
      }
    },
    async startBenchmark(isGradient) {
      this.singleResult = null
      this.gradientResults = []
      this.charts.forEach(c => c && c.dispose())
      this.charts = []
      const cfg = this.$refs.configPanel.getConfig()
      try {
        const endpoint = isGradient ? '/benchmark/run-gradient' : '/benchmark/run'
        const payload = isGradient
          ? { llm_url: cfg.llmUrl, api_key: cfg.apiKey, model: cfg.model, use_long_context: cfg.useLongContext }
          : { ...cfg, llm_url: cfg.llmUrl, api_key: cfg.apiKey, model: cfg.model, use_long_context: cfg.useLongContext }
        await api.post(endpoint, payload)
        this.running = true
        this.progressPercent = 0
        this.progressStatus = ''
        this.progressText = '⏳ 正在初始化...'
        this.startPolling()
      } catch (err) {
        this.$message.error(err.response?.data?.message || '启动失败')
      }
    },
    startPolling() {
      this.pollTimer = setInterval(async () => {
        try {
          const res = await api.get('/task/status')
          const data = res.data
          if (data.status === 'running') {
            this.progressPercent = Math.round((data.completed / data.total) * 100)
            this.progressText = `⏳ 压测进度: ${data.completed} / ${data.total} (${this.progressPercent}%)`
          } else if (data.status === 'completed') {
            this.stopPolling()
            this.running = false
            this.progressPercent = 100
            this.progressStatus = 'success'
            this.progressText = '✅ 压力测试全部完成！'
            if (data.results && Array.isArray(data.results)) {
              this.gradientResults = data.results
            } else {
              this.singleResult = data.result
            }
            if (data.report_files) {
              this.reportFiles = data.report_files
            }
          } else if (data.status === 'failed') {
            this.stopPolling()
            this.running = false
            this.progressStatus = 'exception'
            this.progressText = `❌ 错误: ${data.error}`
            this.$message.error(data.error)
          }
        } catch (err) {
          console.error('Poll error', err)
        }
      }, 2000)
    },
    stopPolling() {
      if (this.pollTimer) {
        clearInterval(this.pollTimer)
        this.pollTimer = null
      }
    },
    async exportSingle(fmt) {
      try {
        const ext = fmt === 'excel' ? 'xlsx' : 'csv'
        const filename = this.reportFiles[ext]
        if (!filename) {
          this.$message.warning('报告文件未就绪，请稍后重试')
          return
        }
        const res = await api.get(`/reports/download/${filename}`, { responseType: 'blob' })
        const url = window.URL.createObjectURL(new Blob([res.data]))
        const link = document.createElement('a')
        link.href = url
        link.setAttribute('download', filename)
        document.body.appendChild(link)
        link.click()
        link.remove()
      } catch (err) {
        this.$message.error('导出失败')
      }
    },
    async exportGradient() {
      try {
        const filename = this.reportFiles.xlsx
        if (!filename) {
          this.$message.warning('报告文件未就绪，请稍后重试')
          return
        }
        const res = await api.get(`/reports/download/${filename}`, { responseType: 'blob' })
        const url = window.URL.createObjectURL(new Blob([res.data]))
        const link = document.createElement('a')
        link.href = url
        link.setAttribute('download', filename)
        document.body.appendChild(link)
        link.click()
        link.remove()
      } catch (err) {
        this.$message.error('导出失败')
      }
    }
  }
}
</script>

<style scoped>
.home-view { padding: 10px 0; }
</style>
