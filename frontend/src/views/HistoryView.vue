<template>
  <AppShell title="测试记录" description="从任务索引复查摘要、失败状态和离线制品。">
    <template #actions>
      <button class="secondary-button" type="button" :disabled="loading" @click="load">
        刷新记录
      </button>
    </template>
    <section class="history-tools panel">
      <label>筛选记录<input v-model.trim="query" placeholder="名称、模型、状态或任务 ID"></label>
      <label>状态<select v-model="status"><option value="">全部状态</option><option v-for="item in statusOptions" :key="item" :value="item">{{ statusLabel(item) }}</option></select></label>
      <span class="result-count mono">{{ filtered.length }} / {{ tasks.length }}</span>
    </section>
    <StateNotice v-if="error" tone="danger" title="记录加载失败" :message="error" />
    <div v-if="!loading && !filtered.length" class="empty-state">
      <strong>还没有符合条件的测试记录</strong><p>完成一次冒烟或基线测试后，可在这里建立历史基准。</p><RouterLink class="primary-button" to="/">
        创建第一项测试
      </RouterLink>
    </div>
    <section v-else class="history-list" aria-label="测试记录列表">
      <article v-for="item in filtered" :key="item.id" class="history-row" :class="item.status">
        <div class="history-state">
          <span class="state-lamp" :class="item.status"></span><small>{{ statusLabel(item.status) }}</small>
        </div>
        <div class="history-main">
          <h2>{{ item.name }}</h2><p class="mono">
            {{ item.id }}
          </p><span>{{ planName(item) }} · {{ modelName(item) }}</span>
        </div>
        <div class="history-metrics">
          <span><small>P95</small><strong>{{ metric(item, 'latency', 'p95') }}</strong></span><span><small>QPS</small><strong>{{ metric(item, null, 'achieved_qps') }}</strong></span><span><small>成功</small><strong>{{ successRate(item) }}</strong></span>
        </div>
        <time>{{ formatTime(item.created_at) }}</time>
        <button type="button" class="secondary-button" @click="openRecord(item)">
          查看详情
        </button>
      </article>
    </section>

    <div v-if="selected" class="drawer-backdrop" @click.self="selected = null">
      <aside class="record-drawer" role="dialog" aria-modal="true" :aria-label="`${selected.name} 详情`">
        <button class="drawer-close" type="button" aria-label="关闭" @click="selected = null">
          ×
        </button>
        <p class="eyebrow">
          TEST RECORD
        </p><h2>{{ selected.name }}</h2>
        <LoadRuler :segments="recordSegments" :progress="1" label="已执行负载" />
        <div class="drawer-metrics">
          <MetricReadout label="完成请求" :value="result.completed_requests" /><MetricReadout label="有效响应" :value="result.valid_responses" /><MetricReadout label="TTFT P95" :value="nested('ttft', 'p95')" unit="s" /><MetricReadout label="输出 TPS" :value="result.aggregate_output_tps" unit="tok/s" />
        </div>
        <StateNotice v-if="selected.error_message" tone="danger" title="执行失败" :message="selected.error_message" />
        <section class="artifact-section">
          <div class="panel-heading">
            <span>ARTIFACTS</span><h3>离线制品</h3>
          </div>
          <StateNotice v-if="artifactNotice" :tone="artifactNotice.tone" :title="artifactNotice.title" :message="artifactNotice.message" />
          <StateNotice v-else-if="!canGenerateReports" tone="warning" title="任务尚无可导出结果" :message="`${statusLabel(selected.status)}任务需要等待结果写入后才能生成报告。`" />
          <div v-if="!reports.length" class="inline-empty">
            还没有制品，可按需生成摘要格式。
          </div>
          <a v-for="report in reports" :key="report.id" class="artifact-row" :href="downloadUrl(report.id)"><strong>{{ report.format.toUpperCase() }}</strong><span>{{ formatBytes(report.size_bytes) }}</span><code>{{ report.sha256.slice(0, 12) }}</code></a>
          <div class="export-actions">
            <button v-for="format in ['json', 'html', 'csv', 'xlsx']" :key="format" type="button" class="secondary-button" :disabled="!canGenerateReports || Boolean(generatingFormat)" @click="generate(format)">
              {{ generatingFormat === format ? '正在生成…' : `生成 ${format.toUpperCase()}` }}
            </button>
          </div>
        </section>
      </aside>
    </div>
  </AppShell>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, reportDownloadUrl } from '../api/client'
import AppShell from '../components/AppShell.vue'
import LoadRuler from '../components/LoadRuler.vue'
import MetricReadout from '../components/MetricReadout.vue'
import StateNotice from '../components/StateNotice.vue'

const route = useRoute()
const tasks = ref([]); const reports = ref([]); const selected = ref(null); const query = ref(''); const status = ref(''); const loading = ref(false); const error = ref('')
const artifactNotice = ref(null)
const generatingFormat = ref('')
const statusOptions = ['queued', 'running', 'stopping', 'completed', 'failed', 'cancelled', 'interrupted']
const filtered = computed(() => tasks.value.filter(item => {
  if (status.value && item.status !== status.value) return false
  const text = `${item.name} ${item.id} ${item.status} ${modelName(item)}`.toLowerCase()
  return !query.value || text.includes(query.value.toLowerCase())
}))
const result = computed(() => selected.value && selected.value.result ? selected.value.result : {})
const canGenerateReports = computed(() => Boolean(selected.value && selected.value.result))
const recordSegments = computed(() => {
  const plan = selected.value && selected.value.payload && selected.value.payload.plan
  if (plan && plan.stages && plan.stages.length) return plan.stages.map(item => ({ name: item.name, detail: `${item.concurrency} 并发`, weight: item.requests || 1, kind: 'load' }))
  return [{ name: '完整负载', detail: plan ? `${plan.concurrency || 1} 并发` : '已结束', weight: 1, kind: selected.value && selected.value.status === 'failed' ? 'danger' : 'load' }]
})

onMounted(async () => { await load(); if (route.query.task) { const item = tasks.value.find(task => task.id === route.query.task); if (item) openRecord(item) } })
async function load() { loading.value = true; error.value = ''; try { tasks.value = await api.tasks() } catch (reason) { error.value = reason.message } finally { loading.value = false } }
async function openRecord(item) {
  artifactNotice.value = null
  selected.value = await api.task(item.id)
  reports.value = await api.reports(item.id)
}
async function generate(format) {
  generatingFormat.value = format
  artifactNotice.value = null
  try {
    await api.generateReport(selected.value.id, format)
    reports.value = await api.reports(selected.value.id)
    artifactNotice.value = { tone: 'success', title: '报告生成完成', message: `${format.toUpperCase()} 制品已写入本地报告目录。` }
  } catch (reason) {
    artifactNotice.value = { tone: 'danger', title: '报告生成失败', message: reason.message || '无法生成报告，请稍后重试。' }
  } finally {
    generatingFormat.value = ''
  }
}
function downloadUrl(id) { return reportDownloadUrl(id) }
function statusLabel(value) { return ({ queued: '排队中', running: '运行中', stopping: '停止中', completed: '已完成', failed: '失败', cancelled: '已停止', interrupted: '已中断' })[value] || value }
function planName(item) { return item.payload && item.payload.plan ? item.payload.plan.plan_type : '未知计划' }
function modelName(item) { return item.payload && item.payload.endpoint && item.payload.endpoint.model ? item.payload.endpoint.model : '已保存连接' }
function metric(item, parent, key) { const value = parent ? item.result && item.result[parent] && item.result[parent][key] : item.result && item.result[key]; return typeof value === 'number' ? value.toFixed(parent ? 3 : 2) : '—' }
function successRate(item) { const resultValue = item.result; return resultValue && resultValue.completed_requests ? `${Math.round((resultValue.transport_successes || 0) / resultValue.completed_requests * 100)}%` : '—' }
function nested(parent, key) { const value = result.value[parent] && result.value[parent][key]; return typeof value === 'number' ? value.toFixed(3) : value }
function formatTime(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }
function formatBytes(value) { if (value < 1024) return `${value} B`; if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1048576).toFixed(1)} MB` }
</script>
