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
        <section v-if="canGenerateReports" class="record-detail-section baseline-section">
          <div class="panel-heading detail-heading">
            <div><span>BASELINE LEDGER</span><h3>历史基线</h3></div><small>{{ baselineCandidates.length }} 个候选</small>
          </div>
          <StateNotice v-if="baselineNotice" :tone="baselineNotice.tone" :title="baselineNotice.title" :message="baselineNotice.message" />
          <div v-if="baselineLoading" class="inline-empty">
            正在核对历史任务的计划、负载和请求维度…
          </div>
          <div v-else-if="!baselineCandidates.length" class="inline-empty">
            暂无更早且包含结果的任务。完成第二次同维度测试后即可建立基线。
          </div>
          <template v-else>
            <div class="baseline-controls">
              <label><span>历史任务</span><select v-model="baselineId" @change="previewBaseline($event.target.value)"><option value="">请选择历史任务</option><option v-for="candidate in baselineCandidates" :key="candidate.id" :value="candidate.id">{{ candidate.name }} · {{ candidate.model || '未知模型' }} · {{ candidate.compatible ? '可比较' : '不兼容' }}</option></select></label>
              <label><span>通用百分比容差</span><div class="baseline-field-unit"><input v-model.number="baselineTolerancePercent" type="number" min="0" step="0.5"><span>%</span></div></label>
              <label><span>比率绝对容差</span><div class="baseline-field-unit"><input v-model.number="baselineRateAbsolute" type="number" min="0" max="1" step="0.001"><span>比例值</span></div></label>
              <div class="baseline-actions">
                <button class="primary-button" type="button" :disabled="!canSaveBaseline || baselineSaving" @click="saveBaseline">
                  {{ baselineSaving ? '正在保存…' : '保存为基线' }}
                </button>
                <button v-if="savedBaseline" class="secondary-button" type="button" :disabled="baselineSaving" @click="deleteBaseline">
                  取消基线
                </button>
              </div>
            </div>
            <article v-if="selectedCandidate" class="baseline-candidate" :class="selectedCandidate.compatible ? 'compatible' : 'incompatible'">
              <header><div><strong>{{ selectedCandidate.name }}</strong><code>{{ selectedCandidate.id }}</code></div><span>{{ selectedCandidate.compatible ? '维度一致' : '不可等价比较' }}</span></header>
              <p>{{ selectedCandidate.plan_type || '未知计划' }} · {{ selectedCandidate.model || '未知模型' }} · {{ formatTime(selectedCandidate.created_at) }}</p>
              <ul v-if="!selectedCandidate.compatible">
                <li v-for="reason in selectedCandidate.incompatibilities" :key="reason">
                  {{ reason }}
                </li>
              </ul>
            </article>
            <div v-if="baselineComparison && baselineComparison.compatible" class="baseline-comparison">
              <div class="baseline-verdict" :class="baselineComparison.conclusion">
                <div><small>比较结论</small><strong>{{ conclusionLabel(baselineComparison.conclusion) }}</strong></div>
                <p>回归 {{ baselineComparison.counts.regressed }} · 改善 {{ baselineComparison.counts.improved }} · 稳定 {{ baselineComparison.counts.stable }} · 不可评估 {{ baselineComparison.counts.not_evaluable }}</p>
              </div>
              <div class="baseline-table" role="table" aria-label="历史基线逐指标比较">
                <div class="baseline-table-head" role="row">
                  <span>指标</span><span>当前</span><span>基线</span><span>绝对差</span><span>变化</span><span>容差</span><span>结论</span>
                </div>
                <div v-for="item in baselineComparison.metrics" :key="item.metric" class="baseline-table-row" role="row">
                  <strong>{{ metricLabel(item.metric) }}</strong><span>{{ metricValue(item.metric, item.current) }}</span><span>{{ metricValue(item.metric, item.baseline) }}</span><span>{{ deltaValue(item) }}</span><span>{{ percentDelta(item.percent_delta) }}</span><span>{{ toleranceLabel(item) }}</span><em :class="item.status">{{ metricStatusLabel(item.status) }}</em>
                </div>
              </div>
            </div>
          </template>
        </section>
        <section v-if="canGenerateReports" class="record-detail-section">
          <div class="panel-heading detail-heading">
            <div><span>TIME WINDOWS</span><h3>时间序列</h3></div><small>{{ timeSeriesCaption }}</small>
          </div>
          <div v-if="displayWindows.length" class="time-window-list" aria-label="任务时间序列">
            <article v-for="window in displayWindows" :key="`${window.start_offset}-${window.end_offset}`" class="time-window-row">
              <code>{{ rangeLabel(window) }}</code>
              <div class="window-track" role="img" :aria-label="windowAriaLabel(window)">
                <span :style="{ width: `${windowWidth(window)}%` }"></span>
              </div>
              <strong>{{ fixed(window.achieved_qps, 2) }} QPS</strong>
              <small>P95 {{ fixed(window.latency_p95, 3) }}s · 错误 {{ window.errors || 0 }}</small>
            </article>
          </div>
          <div v-else class="inline-empty">
            该历史记录未包含时间窗口；新任务完成后会自动保存。
          </div>
        </section>
        <section v-if="canGenerateReports" class="record-detail-section">
          <div class="panel-heading detail-heading">
            <div><span>FAILURE SAMPLES</span><h3>失败样本</h3></div><small>{{ failedSamples.length }} / {{ failedSampleTotal }}</small>
          </div>
          <StateNotice v-if="!failedSampleTotal" tone="success" title="未记录失败样本" message="传输、协议和响应断言均未产生可展示的失败记录。" />
          <div v-else class="failure-sample-list">
            <article v-for="sample in failedSamples" :key="sample.request_id" class="failure-sample">
              <header><code>{{ sample.request_id }}</code><span class="failure-category">{{ errorCategoryLabel(sample.category) }}</span><span v-if="sample.retryable" class="retry-chip">可重试</span></header>
              <p>{{ sample.message }}</p>
              <dl><div><dt>发生时间</dt><dd>{{ seconds(sample.started_at_offset, 2) }}</dd></div><div><dt>状态码</dt><dd>{{ sample.status_code || '—' }}</dd></div><div><dt>延迟</dt><dd>{{ seconds(sample.latency, 3) }}</dd></div><div><dt>TTFT</dt><dd>{{ seconds(sample.ttft, 3) }}</dd></div></dl>
            </article>
          </div>
        </section>
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
const baselineCandidates = ref([])
const baselineId = ref('')
const baselineComparison = ref(null)
const savedBaseline = ref(null)
const baselineLoading = ref(false)
const baselineSaving = ref(false)
const baselineNotice = ref(null)
const baselineTolerancePercent = ref(5)
const baselineRateAbsolute = ref(0.01)
const statusOptions = ['queued', 'running', 'stopping', 'completed', 'failed', 'cancelled', 'interrupted']
const rateMetrics = ['error_rate', 'valid_response_rate', 'assertion_pass_rate']
const filtered = computed(() => tasks.value.filter(item => {
  if (status.value && item.status !== status.value) return false
  const text = `${item.name} ${item.id} ${item.status} ${modelName(item)}`.toLowerCase()
  return !query.value || text.includes(query.value.toLowerCase())
}))
const result = computed(() => selected.value && selected.value.result ? selected.value.result : {})
const canGenerateReports = computed(() => Boolean(selected.value && selected.value.result))
const timeSeries = computed(() => Array.isArray(result.value.time_series) ? result.value.time_series : [])
const displayWindows = computed(() => compactWindows(timeSeries.value))
const maxWindowQps = computed(() => Math.max(1, ...displayWindows.value.map(item => Number(item.achieved_qps) || 0)))
const timeSeriesCaption = computed(() => timeSeries.value.length > displayWindows.value.length ? `${displayWindows.value.length} 组 / ${timeSeries.value.length} 窗口` : `${timeSeries.value.length} 个窗口`)
const failedSamples = computed(() => Array.isArray(result.value.failed_samples) ? result.value.failed_samples : [])
const failedSampleTotal = computed(() => Number(result.value.failed_sample_total) || failedSamples.value.length)
const selectedCandidate = computed(() => baselineCandidates.value.find(item => item.id === baselineId.value) || null)
const canSaveBaseline = computed(() => Boolean(selectedCandidate.value && selectedCandidate.value.compatible && baselineComparison.value && baselineComparison.value.compatible))
const recordSegments = computed(() => {
  const plan = selected.value && selected.value.payload && selected.value.payload.plan
  if (plan && plan.stages && plan.stages.length) return plan.stages.map(item => ({ name: item.name, detail: `${item.concurrency} 并发`, weight: item.requests || 1, kind: 'load' }))
  return [{ name: '完整负载', detail: plan ? `${plan.concurrency || 1} 并发` : '已结束', weight: 1, kind: selected.value && selected.value.status === 'failed' ? 'danger' : 'load' }]
})

onMounted(async () => { await load(); if (route.query.task) { const item = tasks.value.find(task => task.id === route.query.task); if (item) openRecord(item) } })
async function load() { loading.value = true; error.value = ''; try { tasks.value = await api.tasks() } catch (reason) { error.value = reason.message } finally { loading.value = false } }
async function openRecord(item) {
  artifactNotice.value = null
  baselineNotice.value = null
  baselineCandidates.value = []
  baselineId.value = ''
  baselineComparison.value = null
  savedBaseline.value = null
  const detail = await api.task(item.id)
  selected.value = detail
  reports.value = await api.reports(item.id)
  if (detail.result) await loadBaselineState(detail.id)
}
async function loadBaselineState(taskId) {
  baselineLoading.value = true
  try {
    baselineCandidates.value = await api.baselineCandidates(taskId)
    try {
      const relation = await api.savedBaseline(taskId)
      if (relation) {
        savedBaseline.value = relation
        baselineId.value = relation.baseline_task_id
        baselineComparison.value = relation.comparison
        applySavedTolerances(relation.comparison)
      }
    } catch (reason) {
      if (reason.status !== 404) throw reason
    }
  } catch (reason) {
    baselineNotice.value = { tone: 'danger', title: '基线加载失败', message: reason.message || '无法读取历史基线。' }
  } finally {
    baselineLoading.value = false
  }
}
async function previewBaseline(candidateId) {
  baselineId.value = candidateId
  baselineNotice.value = null
  baselineComparison.value = null
  const candidate = baselineCandidates.value.find(item => item.id === candidateId) || null
  if (!candidate) return
  if (!candidate.compatible) {
    baselineComparison.value = { compatible: false, incompatibilities: candidate.incompatibilities, conclusion: 'incompatible', metrics: [] }
    return
  }
  const requestedId = candidate.id
  try {
    const comparison = await api.previewBaseline(selected.value.id, requestedId)
    if (baselineId.value === requestedId) baselineComparison.value = comparison
  } catch (reason) {
    baselineNotice.value = { tone: 'danger', title: '比较失败', message: reason.message || '无法比较所选任务。' }
  }
}
async function saveBaseline() {
  baselineSaving.value = true
  baselineNotice.value = null
  try {
    const relation = await api.saveBaseline(selected.value.id, {
      baseline_id: baselineId.value,
      tolerances: buildBaselineTolerances()
    })
    savedBaseline.value = relation
    baselineComparison.value = relation.comparison
    baselineNotice.value = { tone: 'success', title: '历史基线已保存', message: '比较快照和容差已写入本地数据库。' }
  } catch (reason) {
    baselineNotice.value = { tone: 'danger', title: '基线保存失败', message: reason.message || '无法保存历史基线。' }
  } finally {
    baselineSaving.value = false
  }
}
async function deleteBaseline() {
  baselineSaving.value = true
  baselineNotice.value = null
  try {
    await api.deleteBaseline(selected.value.id)
    savedBaseline.value = null
    baselineNotice.value = { tone: 'success', title: '历史基线已取消', message: '任务结果仍保留，可随时重新选择。' }
  } catch (reason) {
    baselineNotice.value = { tone: 'danger', title: '取消失败', message: reason.message || '无法取消历史基线。' }
  } finally {
    baselineSaving.value = false
  }
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
function compactWindows(rows, limit = 36) {
  if (rows.length <= limit) return rows
  const size = Math.ceil(rows.length / limit)
  const compacted = []
  for (let index = 0; index < rows.length; index += size) {
    const bucket = rows.slice(index, index + size)
    compacted.push({
      start_offset: bucket[0].start_offset,
      end_offset: bucket[bucket.length - 1].end_offset,
      achieved_qps: Math.max(...bucket.map(item => Number(item.achieved_qps) || 0)),
      latency_p95: maxDefined(bucket.map(item => item.latency_p95)),
      errors: bucket.reduce((total, item) => total + (Number(item.errors) || 0), 0)
    })
  }
  return compacted
}
function maxDefined(values) {
  const numbers = values.filter(value => typeof value === 'number')
  return numbers.length ? Math.max(...numbers) : null
}
function windowWidth(window) { return Math.max(2, Math.min(100, (Number(window.achieved_qps) || 0) / maxWindowQps.value * 100)) }
function rangeLabel(window) { return `${fixed(window.start_offset, 1)}–${fixed(window.end_offset, 1)}s` }
function windowAriaLabel(window) { return `${rangeLabel(window)}，QPS ${fixed(window.achieved_qps, 2)}，P95 延迟 ${fixed(window.latency_p95, 3)} 秒，错误 ${window.errors || 0}` }
function fixed(value, digits) { return typeof value === 'number' ? value.toFixed(digits) : '—' }
function seconds(value, digits) { return typeof value === 'number' ? `${value.toFixed(digits)}s` : '—' }
function buildBaselineTolerances() {
  const comparison = baselineComparison.value
  if (!comparison || !Array.isArray(comparison.metrics)) return {}
  const percent = Math.max(0, Number(baselineTolerancePercent.value) || 0)
  const rateAbsolute = Math.max(0, Number(baselineRateAbsolute.value) || 0)
  return comparison.metrics.reduce((values, item) => {
    values[item.metric] = { percent, absolute: rateMetrics.includes(item.metric) ? rateAbsolute : 0 }
    return values
  }, {})
}
function applySavedTolerances(comparison) {
  if (!comparison || !Array.isArray(comparison.metrics)) return
  const percentMetric = comparison.metrics.find(item => typeof item.tolerance_percent === 'number')
  const rateMetric = comparison.metrics.find(item => rateMetrics.includes(item.metric))
  if (percentMetric) baselineTolerancePercent.value = percentMetric.tolerance_percent
  if (rateMetric && typeof rateMetric.tolerance_absolute === 'number') baselineRateAbsolute.value = rateMetric.tolerance_absolute
}
function metricLabel(value) { return ({ 'latency.mean': '端到端平均延迟', 'latency.p50': '端到端 P50', 'latency.p90': '端到端 P90', 'latency.p95': '端到端 P95', 'latency.p99': '端到端 P99', 'ttft.mean': 'TTFT 平均', 'ttft.p50': 'TTFT P50', 'ttft.p95': 'TTFT P95', 'ttft.p99': 'TTFT P99', 'generation_duration.mean': '生成时长平均', 'request_output_tps.mean': '单请求输出 TPS', achieved_qps: '实际 QPS', aggregate_output_tps: '聚合输出 TPS', error_rate: '错误率', valid_response_rate: '有效响应率', assertion_pass_rate: '断言通过率' })[value] || value }
function metricValue(metricName, value) { if (typeof value !== 'number') return '—'; if (rateMetrics.includes(metricName)) return `${(value * 100).toFixed(2)}%`; if (metricName.startsWith('latency.') || metricName.startsWith('ttft.') || metricName.startsWith('generation_duration.')) return `${value.toFixed(3)}s`; return value.toFixed(2) }
function deltaValue(item) { if (typeof item.absolute_delta !== 'number') return '—'; const prefix = item.absolute_delta > 0 ? '+' : ''; if (rateMetrics.includes(item.metric)) return `${prefix}${(item.absolute_delta * 100).toFixed(2)}pp`; if (item.metric.startsWith('latency.') || item.metric.startsWith('ttft.') || item.metric.startsWith('generation_duration.')) return `${prefix}${item.absolute_delta.toFixed(3)}s`; return `${prefix}${item.absolute_delta.toFixed(2)}` }
function percentDelta(value) { if (typeof value !== 'number') return '—'; return `${value > 0 ? '+' : ''}${value.toFixed(2)}%` }
function toleranceLabel(item) { const percent = typeof item.tolerance_percent === 'number' ? `${item.tolerance_percent.toFixed(1)}%` : '无百分比'; const absolute = rateMetrics.includes(item.metric) ? `${(item.tolerance_absolute * 100).toFixed(2)}pp` : metricValue(item.metric, item.tolerance_absolute); return `${percent} / ${absolute}` }
function conclusionLabel(value) { return ({ regressed: '存在性能回归', improved: '整体有所改善', stable: '处于容差范围', not_evaluable: '指标不足', incompatible: '维度不兼容' })[value] || value }
function metricStatusLabel(value) { return ({ regressed: '回归', improved: '改善', stable: '稳定', not_evaluable: '不可评估' })[value] || value }
function errorCategoryLabel(value) { return ({ timeout: '超时', authentication: '认证', rate_limit: '限流', server: '服务端', client: '客户端', transport: '传输', protocol: '协议', cancelled: '取消', assertion: '断言', unknown: '未知' })[value] || value || '未知' }
function formatTime(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }
function formatBytes(value) { if (value < 1024) return `${value} B`; if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1048576).toFixed(1)} MB` }
</script>
