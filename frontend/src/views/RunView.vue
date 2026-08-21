<template>
  <AppShell title="运行监控" description="浏览器断开不会终止任务；重连后从最后一个事件继续。">
    <template #actions>
      <button v-if="canStop" type="button" class="danger-button" :disabled="stopping" @click="stopTask">
        {{ stopping ? '正在请求停止…' : '安全停止' }}
      </button>
    </template>

    <StateNotice v-if="error" tone="danger" title="监控连接异常" :message="error" />
    <div v-if="!task" class="empty-state">
      <strong>没有可监控的任务</strong><p>创建测试后，任务会在这里显示实时状态。</p><RouterLink class="primary-button" to="/">
        新建测试
      </RouterLink>
    </div>
    <template v-else>
      <LoadRuler :segments="segments" :progress="progress" :label="task.name" />
      <section class="run-strip">
        <div><span class="state-lamp" :class="task.status"></span><small>任务状态</small><strong>{{ statusLabel }}</strong></div>
        <MetricReadout label="已完成" :value="completed" :unit="`/ ${total || '—'}`" />
        <MetricReadout label="实时 QPS" :value="metric('achieved_qps')" unit="req/s" />
        <MetricReadout label="P95 延迟" :value="nestedMetric('latency', 'p95')" unit="s" />
        <MetricReadout label="聚合输出" :value="metric('aggregate_output_tps')" unit="tok/s" />
        <MetricReadout label="错误" :value="errorCount" unit="次" :tone="errorCount ? 'danger' : ''" />
      </section>

      <section v-if="thresholdPanel" class="threshold-verdict" :class="thresholdPanel.status" aria-labelledby="run-threshold-title">
        <header class="threshold-verdict-summary">
          <div>
            <span>THRESHOLD VERDICT</span>
            <h2 id="run-threshold-title">
              性能阈值
            </h2>
            <small>{{ thresholdPanel.name || '未命名模板' }}</small>
          </div>
          <strong>{{ thresholdStatusLabel(thresholdPanel.status) }}</strong>
          <p>{{ thresholdSummary }}</p>
        </header>
        <div class="threshold-result-list" role="list" aria-label="性能阈值逐项结果">
          <article v-for="item in thresholdPanel.results" :key="`${item.metric}-${item.operator}`" class="threshold-result-row" :class="item.status" role="listitem">
            <div><small>{{ thresholdMetricLabel(item.metric) }}</small><strong>{{ thresholdMetricValue(item.metric, item.observed) }}</strong></div>
            <code>{{ item.operator }} {{ thresholdMetricValue(item.metric, item.expected) }}</code>
            <em>{{ thresholdRuleStatusLabel(item.status) }}</em>
          </article>
        </div>
      </section>

      <div class="run-grid">
        <section class="panel event-panel">
          <div class="panel-heading">
            <span>LIVE TRACE</span><h2>持久事件流</h2><small>{{ transportLabel }}</small>
          </div>
          <ol class="event-list" aria-live="polite">
            <li v-for="event in events.slice(-30).reverse()" :key="event.sequence">
              <time>{{ formatElapsed(event.elapsed) }}</time>
              <span class="event-phase">{{ phaseLabel(event.phase) }}</span>
              <strong>{{ eventLabel(event.event_type) }}</strong>
              <small>{{ eventSummary(event) }}</small>
            </li>
          </ol>
        </section>
        <aside class="panel run-detail">
          <div class="panel-heading">
            <span>SNAPSHOT</span><h2>执行快照</h2>
          </div>
          <dl class="detail-list">
            <div>
              <dt>任务 ID</dt><dd class="mono">
                {{ task.id }}
              </dd>
            </div>
            <div><dt>计划</dt><dd>{{ planLabel }}</dd></div>
            <div><dt>创建时间</dt><dd>{{ formatTime(task.created_at) }}</dd></div>
            <div><dt>开始时间</dt><dd>{{ formatTime(task.started_at) }}</dd></div>
            <div><dt>队列位置</dt><dd>{{ task.queue_position || '—' }}</dd></div>
            <div><dt>停止原因</dt><dd>{{ task.stopped_reason || '—' }}</dd></div>
          </dl>
          <div v-if="isTerminal" class="export-box">
            <strong>实验已结束</strong><p>摘要和样本已保存在服务器数据卷中。</p>
            <RouterLink class="primary-button" :to="`/history?task=${task.id}`">
              查看记录与导出
            </RouterLink>
          </div>
        </aside>
      </div>
    </template>
  </AppShell>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api/client'
import { useTaskStore } from '../stores/tasks'
import AppShell from '../components/AppShell.vue'
import LoadRuler from '../components/LoadRuler.vue'
import MetricReadout from '../components/MetricReadout.vue'
import StateNotice from '../components/StateNotice.vue'

const route = useRoute()
const router = useRouter()
const store = useTaskStore()
const task = ref(null)
const events = ref([])
const error = ref('')
const transport = ref('idle')
const stopping = ref(false)
let source = null
let pollTimer = null
let fallbackTimer = null

const isTerminal = computed(() => task.value && ['completed', 'failed', 'cancelled', 'interrupted'].includes(task.value.status))
const canStop = computed(() => task.value && ['queued', 'running'].includes(task.value.status))
const total = computed(() => task.value && task.value.payload && task.value.payload.plan ? task.value.payload.plan.total_requests : null)
const completed = computed(() => {
  const result = task.value && task.value.result
  if (result && result.completed_requests !== undefined) return result.completed_requests
  const event = [...events.value].reverse().find(item => item.payload && item.payload.completed !== undefined)
  return event ? event.payload.completed : 0
})
const progress = computed(() => total.value ? Math.min(1, completed.value / total.value) : (isTerminal.value ? 1 : 0.08))
const errorCount = computed(() => {
  const counts = task.value && task.value.result && task.value.result.error_counts
  return counts ? Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0) : 0
})
const statusLabels = { queued: '等待执行', running: '正在加载', stopping: '正在安全停止', completed: '完成', failed: '失败', cancelled: '已停止', interrupted: '服务重启中断' }
const statusLabel = computed(() => statusLabels[task.value.status] || task.value.status)
const transportLabel = computed(() => ({ sse: 'SSE 实时连接', reconnecting: 'SSE 正在重连', polling: '轮询回退 · 2 秒', idle: '等待连接' })[transport.value])
const planLabel = computed(() => task.value && task.value.payload && task.value.payload.plan ? task.value.payload.plan.plan_type : '—')
const thresholdEvaluation = computed(() => task.value && task.value.result ? task.value.result.thresholds : null)
const thresholdSnapshot = computed(() => task.value && task.value.payload ? task.value.payload.thresholds : null)
const thresholdPanel = computed(() => {
  if (thresholdEvaluation.value) return thresholdEvaluation.value
  if (!thresholdSnapshot.value) return null
  const status = isTerminal.value ? 'not_evaluable' : 'pending'
  return {
    name: thresholdSnapshot.value.name,
    status,
    counts: null,
    results: (thresholdSnapshot.value.rules || []).map(item => ({
      metric: item.metric,
      operator: item.operator,
      expected: item.value,
      observed: null,
      status
    }))
  }
})
const thresholdSummary = computed(() => {
  if (!thresholdPanel.value) return ''
  if (thresholdPanel.value.status === 'pending') return '任务结束后使用本次固化模板逐项评估。'
  const counts = thresholdPanel.value.counts
  if (!counts) return '任务未生成完整摘要，所选规则无法评估。'
  return `通过 ${counts.passed || 0} · 未达标 ${counts.failed || 0} · 不可评估 ${counts.not_evaluable || 0}`
})
const segments = computed(() => {
  const plan = task.value && task.value.payload && task.value.payload.plan
  if (!plan) return []
  if (plan.stages && plan.stages.length) return plan.stages.map(item => ({ name: item.name, detail: `${item.concurrency} 并发`, weight: item.requests || item.duration_seconds || 1, kind: 'load' }))
  return [
    ...(plan.warmup_seconds ? [{ name: '预热', detail: `${plan.warmup_seconds}s`, weight: 1, kind: 'warmup' }] : []),
    { name: '负载', detail: plan.target_qps ? `${plan.target_qps} QPS` : `${plan.concurrency} 并发`, weight: 6, kind: task.value.status === 'failed' ? 'danger' : 'load' },
    ...(plan.cooldown_seconds ? [{ name: '冷却', detail: `${plan.cooldown_seconds}s`, weight: 1, kind: 'cooldown' }] : [])
  ]
})

onMounted(loadInitial)
watch(() => route.params.id, loadInitial)
onBeforeUnmount(cleanup)

async function loadInitial() {
  cleanup(); error.value = ''; events.value = []
  try {
    let id = route.params.id
    if (!id) {
      await store.refresh()
      const preferred = store.active[0] || store.items[0]
      if (!preferred) return
      id = preferred.id
      router.replace(`/runs/${id}`)
      return
    }
    task.value = await api.task(id)
    if (!isTerminal.value) connectEvents(id)
  } catch (reason) { error.value = reason.message }
}
function connectEvents(id) {
  if (!window.EventSource) { startPolling(id); return }
  const url = `/api/tasks/${encodeURIComponent(id)}/events`
  source = new EventSource(url, { withCredentials: true })
  transport.value = 'sse'
  source.onopen = () => {
    transport.value = 'sse'
    if (fallbackTimer) window.clearTimeout(fallbackTimer)
    fallbackTimer = null
  }
  source.onmessage = handleMessage
  const types = ['task_started', 'request_completed', 'stage_started', 'stage_completed', 'task_finished', 'terminal', 'started']
  types.forEach(type => source.addEventListener(type, handleMessage))
  source.onerror = () => {
    if (isTerminal.value || fallbackTimer) return
    transport.value = 'reconnecting'
    fallbackTimer = window.setTimeout(() => {
      if (source) source.close()
      source = null
      startPolling(id)
    }, 8000)
  }
}
function handleMessage(event) {
  if (event.type === 'terminal') { refreshTask(); cleanup(); return }
  try {
    const value = JSON.parse(event.data)
    if (value.sequence && !events.value.some(item => item.sequence === value.sequence)) events.value.push(value)
    if (['request_completed', 'task_finished'].includes(value.event_type)) refreshTask()
  } catch (reason) { error.value = `事件解析失败：${reason.message}` }
}
function startPolling(id) {
  transport.value = 'polling'
  const tick = async () => {
    try {
      task.value = await api.task(id)
      if (isTerminal.value) { cleanup(); return }
    } catch (reason) { error.value = reason.message }
    pollTimer = window.setTimeout(tick, 2000)
  }
  tick()
}
async function refreshTask() { if (task.value) task.value = await api.task(task.value.id) }
async function stopTask() {
  stopping.value = true
  try { task.value = await api.cancelTask(task.value.id) }
  catch (reason) { error.value = reason.message }
  finally { stopping.value = false }
}
function cleanup() {
  if (source) source.close()
  source = null
  if (pollTimer) window.clearTimeout(pollTimer)
  pollTimer = null
  if (fallbackTimer) window.clearTimeout(fallbackTimer)
  fallbackTimer = null
  transport.value = 'idle'
}
function metric(name) {
  const value = task.value && task.value.result && task.value.result[name]
  return typeof value === 'number' ? value.toFixed(2) : value
}
function nestedMetric(parent, name) {
  const value = task.value && task.value.result && task.value.result[parent] && task.value.result[parent][name]
  return typeof value === 'number' ? value.toFixed(3) : value
}
function thresholdMetricLabel(value) {
  return ({
    'latency.mean': '端到端平均延迟',
    'latency.p50': '端到端 P50',
    'latency.p90': '端到端 P90',
    'latency.p95': '端到端 P95',
    'latency.p99': '端到端 P99',
    'ttft.mean': 'TTFT 平均',
    'ttft.p50': 'TTFT P50',
    'ttft.p95': 'TTFT P95',
    'ttft.p99': 'TTFT P99',
    'generation_duration.mean': '生成时长平均',
    'request_output_tps.mean': '单请求输出 TPS',
    achieved_qps: '实际 QPS',
    aggregate_output_tps: '聚合输出 TPS',
    error_rate: '错误率',
    valid_response_rate: '有效响应率',
    assertion_pass_rate: '断言通过率'
  })[value] || value
}
function thresholdMetricValue(metricName, value) {
  if (typeof value !== 'number') return '—'
  if (['error_rate', 'valid_response_rate', 'assertion_pass_rate'].includes(metricName)) return `${(value * 100).toFixed(2)}%`
  if (metricName.startsWith('latency.') || metricName.startsWith('ttft.') || metricName.startsWith('generation_duration.')) return `${value.toFixed(3)}s`
  return value.toFixed(2)
}
function thresholdStatusLabel(value) { return ({ passed: '全部通过', failed: '存在未达标', not_evaluable: '部分不可评估', pending: '等待评估' })[value] || value }
function thresholdRuleStatusLabel(value) { return ({ passed: '通过', failed: '未达标', not_evaluable: '不可评估', pending: '待评估' })[value] || value }
function formatElapsed(value) { return `${Number(value || 0).toFixed(1)}s` }
function formatTime(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }
function phaseLabel(value) { return ({ connecting: '连接', warmup: '预热', load: '负载', cooldown: '冷却', finished: '结束' })[value] || value }
function eventLabel(value) { return ({ request_completed: '请求完成', request_scheduled: '请求已调度', stage_started: '阶段开始', stage_completed: '阶段结束', task_started: '任务开始', task_finished: '任务结束' })[value] || value }
function eventSummary(event) {
  const payload = event.payload || {}
  if (payload.completed !== undefined) return `完成 ${payload.completed}${total.value ? ` / ${total.value}` : ''}`
  if (payload.stage) return String(payload.stage)
  return ''
}
</script>
