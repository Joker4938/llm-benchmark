<template>
  <AppShell title="新建性能实验" description="先定义可复现的负载，再让测试台发出请求。">
    <template #actions>
      <div class="head-actions">
        <span class="mode-chip">{{ executionLabel }}</span>
        <span class="mode-chip">{{ form.plan.plan_type === 'stepped' ? '阶梯负载' : '单段负载' }}</span>
      </div>
    </template>

    <LoadRuler :segments="rulerSegments" :progress="0" label="计划负载轨迹" />

    <form class="bench-grid" @submit.prevent="submitTask">
      <section class="panel execution-switch" aria-labelledby="execution-mode-title">
        <div>
          <p class="section-kicker">
            EXECUTION / MODE
          </p>
          <h2 id="execution-mode-title">
            这次实验测一个模型，还是并排比较？
          </h2>
          <p>模型比较始终复用同一计划、数据集和样本顺序；默认逐个运行，不叠加瞬时负载。</p>
        </div>
        <div class="execution-options" role="radiogroup" aria-label="实验执行方式">
          <label class="execution-option" :class="{ active: form.execution_mode === 'single' }">
            <input v-model="form.execution_mode" type="radio" value="single">
            <span><strong>单模型测试</strong><small>一个模型加入本地队列</small></span>
          </label>
          <label class="execution-option" :class="{ active: form.execution_mode === 'comparison' }">
            <input v-model="form.execution_mode" type="radio" value="comparison">
            <span><strong>模型对比</strong><small>两个以上模型复用同一负载</small></span>
          </label>
        </div>
      </section>

      <section class="panel config-panel">
        <div class="panel-heading">
          <span>CONNECTION</span><h2>{{ isComparison ? '比较目标与运行语义' : '连接与模型' }}</h2>
        </div>

        <template v-if="!isComparison">
          <label>已保存连接
            <select v-model="form.api_config_id" required>
              <option value="" disabled>选择 API 配置</option>
              <option v-for="item in configs" :key="item.id" :value="item.id">{{ item.name }} · {{ item.model }}</option>
            </select>
          </label>
          <div v-if="selectedConfig" class="connection-plate">
            <span class="status-dot"></span><strong>{{ selectedConfig.model }}</strong>
            <small>{{ selectedConfig.base_url }}</small><code>{{ selectedConfig.api_key }}</code>
          </div>
        </template>

        <template v-else>
          <div class="comparison-targets" aria-label="模型比较目标">
            <article v-for="(target, index) in form.comparison.targets" :key="target.key" class="target-lane">
              <span class="target-index">M{{ String(index + 1).padStart(2, '0') }}</span>
              <label>模型连接
                <select v-model="target.api_config_id" required>
                  <option value="" disabled>选择不同的 API 配置</option>
                  <option v-for="item in availableTargetConfigs(target.api_config_id)" :key="item.id" :value="item.id">{{ item.name }} · {{ item.model }}</option>
                </select>
              </label>
              <div v-if="configById(target.api_config_id)" class="target-endpoint">
                <strong>{{ configById(target.api_config_id).model }}</strong>
                <small>{{ configById(target.api_config_id).base_url }}</small>
              </div>
              <button
                class="icon-button"
                type="button"
                :disabled="form.comparison.targets.length <= 2"
                :aria-label="`移除模型 ${index + 1}`"
                @click="removeComparisonTarget(index)"
              >
                ×
              </button>
            </article>
          </div>
          <div class="comparison-target-actions">
            <button class="secondary-button" type="button" :disabled="form.comparison.targets.length >= Math.min(configs.length, 10)" @click="addComparisonTarget">
              增加比较模型
            </button>
            <span>{{ form.comparison.targets.length }} 个模型 · 样本顺序固定复用</span>
          </div>
          <p v-if="comparisonIssue" class="field-error" role="alert">
            {{ comparisonIssue }}
          </p>

          <div class="comparison-controls">
            <fieldset>
              <legend>调度方式</legend>
              <label class="choice-card" :class="{ active: form.comparison.mode === 'sequential' }">
                <input v-model="form.comparison.mode" type="radio" value="sequential">
                <span><strong>顺序运行</strong><small>默认；任一时刻只压测一个模型</small></span>
              </label>
              <label class="choice-card" :class="{ active: form.comparison.mode === 'synchronous' }">
                <input v-model="form.comparison.mode" type="radio" value="synchronous">
                <span><strong>同步运行</strong><small>所有模型同时启动，总负载相加</small></span>
              </label>
            </fieldset>
            <fieldset>
              <legend>资源语义</legend>
              <label class="choice-card" :class="{ active: form.comparison.resource_semantics === 'independent' }">
                <input v-model="form.comparison.resource_semantics" type="radio" value="independent">
                <span><strong>独立端点</strong><small>各模型容量可独立解释</small></span>
              </label>
              <label class="choice-card" :class="{ active: form.comparison.resource_semantics === 'shared' }">
                <input v-model="form.comparison.resource_semantics" type="radio" value="shared">
                <span><strong>共享资源</strong><small>结果包含资源竞争效应</small></span>
              </label>
            </fieldset>
          </div>

          <section class="load-bus" aria-label="模型比较负载汇总">
            <header>
              <div><span>LOAD BUS</span><strong>{{ form.comparison.mode === 'synchronous' ? '同步叠加' : '顺序复用' }}</strong></div>
              <small>{{ form.comparison.mode === 'synchronous' ? '所有通道同时激活' : '同一时间仅激活一条通道' }}</small>
            </header>
            <div class="load-lanes">
              <div v-for="(target, index) in form.comparison.targets" :key="`load-${target.key}`" class="load-lane">
                <span>M{{ String(index + 1).padStart(2, '0') }}</span>
                <i></i>
                <strong>{{ configById(target.api_config_id)?.model || '待选择' }}</strong>
                <small>{{ perModelLoadLabel }}</small>
              </div>
            </div>
            <div class="aggregate-load">
              <span>总生成负载</span>
              <strong>{{ aggregateLoad.maxConcurrency }} 并发</strong>
              <small>{{ aggregateLoad.targetQps === null ? '未限制目标 QPS' : `${formatNumber(aggregateLoad.targetQps)} QPS` }} · 同时活动 {{ aggregateLoad.activeModels }} 个模型</small>
            </div>
          </section>

          <StateNotice
            v-if="form.comparison.resource_semantics === 'shared'"
            tone="warning"
            title="共享资源竞争测试"
            message="最终差异同时反映模型性能和共享 GPU、调度器或网关的竞争效应，不能解释为彼此独立的容量。"
          />
        </template>

        <div class="field-row">
          <label>测试名称<input v-model.trim="form.name" required maxlength="128"></label>
          <label>计划类型<select v-model="form.plan.plan_type" @change="normalisePlan">
            <option value="smoke">冒烟测试</option><option value="baseline">基线测试</option>
            <option value="fixed_concurrency">固定并发</option><option value="stepped">阶梯容量</option>
            <option value="constant_rate">恒定 QPS</option><option value="stability">稳定性</option>
          </select></label>
        </div>

        <div v-if="form.plan.plan_type === 'stepped'" class="stage-editor">
          <div v-for="(stage, index) in form.plan.stages" :key="index" class="stage-row">
            <span class="stage-number">{{ String(index + 1).padStart(2, '0') }}</span>
            <label>阶段<input v-model="stage.name"></label>
            <label>并发<input v-model.number="stage.concurrency" type="number" min="1"></label>
            <label>请求<input v-model.number="stage.requests" type="number" min="1"></label>
            <button type="button" class="icon-button" aria-label="删除阶段" @click="removeStage(index)">
              ×
            </button>
          </div>
          <button type="button" class="secondary-button" @click="addStage">
            增加阶段
          </button>
        </div>
        <div v-else class="field-row three">
          <label>并发数<input v-model.number="form.plan.concurrency" type="number" min="1" max="500"></label>
          <label v-if="form.plan.plan_type !== 'stability'">请求数<input v-model.number="form.plan.total_requests" type="number" min="1"></label>
          <label v-if="form.plan.plan_type === 'constant_rate'">目标 QPS<input v-model.number="form.plan.target_qps" type="number" min="0.1" step="0.1"></label>
          <label v-if="['constant_rate', 'stability'].includes(form.plan.plan_type)">时长（秒）<input v-model.number="form.plan.duration_seconds" type="number" min="1"></label>
        </div>
        <div class="field-row three">
          <label>预热（秒）<input v-model.number="form.plan.warmup_seconds" type="number" min="0"></label>
          <label>冷却（秒）<input v-model.number="form.plan.cooldown_seconds" type="number" min="0"></label>
          <label>输出 Token<input v-model.number="form.workload.output_size" type="number" min="1" max="32768"></label>
        </div>
      </section>

      <aside class="panel preflight-panel">
        <div class="panel-heading">
          <span>GUARD RAIL</span><h2>验证与安全预检</h2>
        </div>
        <label>测试数据
          <select v-model="form.workload.dataset_id"><option value="">内置通用短文本</option><option v-for="item in datasets" :key="item.id" :value="item.id">{{ item.name }}</option></select>
        </label>
        <label>导出格式</label>
        <div class="check-grid">
          <label v-for="format in availableFormats" :key="format.value" class="check-option"><input v-model="form.formats" type="checkbox" :value="format.value"><span>{{ format.label }}</span></label>
        </div>
        <div class="safety-readout">
          <span>预计请求</span><strong>{{ estimatedRequests }}</strong>
          <small>{{ isComparison ? '总负载' : '最大负载' }} {{ preflightConcurrency }}</small>
        </div>
        <StateNotice v-if="notice" :tone="notice.tone" :title="notice.title" :message="notice.message" />
        <label v-if="needsConfirmation" class="risk-confirm"><input v-model="form.risk_confirmed" type="checkbox"><span>我已确认高并发 / 高 QPS / 长时间运行会叠加内网服务负载</span></label>
        <label v-if="isSynchronous" class="sync-confirm">
          <input v-model="form.comparison.confirm_synchronous" type="checkbox">
          <span><strong>确认同步叠加负载</strong><small>本次将同时运行 {{ form.comparison.targets.length }} 个模型，总并发与总 QPS 按上方读数叠加。</small></span>
        </label>
        <div class="submit-stack">
          <button type="button" class="secondary-button" :disabled="busy || !canConfigure" @click="runPreflight">
            执行安全预检
          </button>
          <button class="primary-button" type="submit" :disabled="busy || !preflight || !canSubmit">
            {{ busy ? '正在处理…' : submitLabel }}
          </button>
        </div>
      </aside>
    </form>
  </AppShell>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import AppShell from '../components/AppShell.vue'
import LoadRuler from '../components/LoadRuler.vue'
import StateNotice from '../components/StateNotice.vue'

const router = useRouter()
const configs = ref([])
const datasets = ref([])
const preflight = ref(null)
const notice = ref(null)
const needsConfirmation = ref(false)
const busy = ref(false)
let targetSequence = 2
const availableFormats = [
  { value: 'json', label: 'JSON 摘要' }, { value: 'jsonl.gz', label: '样本 JSONL' },
  { value: 'events.jsonl.gz', label: '时间序列' }, { value: 'html', label: '离线 HTML' },
  { value: 'xlsx', label: 'XLSX' }, { value: 'csv', label: 'CSV' }
]
const form = reactive({
  name: `性能实验-${new Date().toISOString().slice(0, 16).replace('T', '-')}`,
  execution_mode: 'single',
  api_config_id: '',
  comparison: {
    mode: 'sequential',
    resource_semantics: 'independent',
    confirm_synchronous: false,
    targets: [{ key: 'target-1', api_config_id: '' }, { key: 'target-2', api_config_id: '' }]
  },
  plan: { plan_type: 'fixed_concurrency', concurrency: 4, total_requests: 40, warmup_seconds: 2, cooldown_seconds: 1, stages: [] },
  workload: { output_size: 128, dataset_id: '' },
  stream: true,
  formats: ['json', 'jsonl.gz', 'events.jsonl.gz', 'html'],
  risk_confirmed: false
})
const isComparison = computed(() => form.execution_mode === 'comparison')
const isSynchronous = computed(() => isComparison.value && form.comparison.mode === 'synchronous')
const selectedConfig = computed(() => configById(form.api_config_id))
const selectedTargetIds = computed(() => form.comparison.targets.map(target => target.api_config_id).filter(Boolean))
const comparisonIssue = computed(() => {
  if (configs.value.length < 2) return '模型比较至少需要两个已保存的 API 配置。'
  if (selectedTargetIds.value.length !== form.comparison.targets.length) return '请为每个模型通道选择连接。'
  if (new Set(selectedTargetIds.value).size !== selectedTargetIds.value.length) return '同一 API 配置不能重复加入比较。'
  return ''
})
const canConfigure = computed(() => isComparison.value ? !comparisonIssue.value : Boolean(form.api_config_id))
const canSubmit = computed(() => canConfigure.value && form.formats.length > 0 && (!isSynchronous.value || form.comparison.confirm_synchronous))
const executionLabel = computed(() => isComparison.value ? `${form.comparison.targets.length} 模型对比` : '单模型测试')
const submitLabel = computed(() => isComparison.value ? '创建模型比较任务' : '加入本地任务队列')
const maxConcurrency = computed(() => {
  const values = [Number(form.plan.concurrency) || 1, ...form.plan.stages.map(stage => Number(stage.concurrency) || 1)]
  return Math.max(...values)
})
const targetQps = computed(() => {
  const values = [form.plan.target_qps, ...form.plan.stages.map(stage => stage.target_qps)].filter(value => value !== undefined && value !== null && value !== '')
  return values.length ? Math.max(...values.map(Number)) : null
})
const aggregateLoad = computed(() => {
  const activeModels = isSynchronous.value ? form.comparison.targets.length : 1
  return {
    activeModels,
    maxConcurrency: maxConcurrency.value * activeModels,
    targetQps: targetQps.value === null ? null : targetQps.value * activeModels
  }
})
const perModelLoadLabel = computed(() => `${maxConcurrency.value} 并发${targetQps.value === null ? '' : ` · ${formatNumber(targetQps.value)} QPS`}`)
const estimatedRequests = computed(() => {
  if (!preflight.value) return '待预检'
  return preflight.value.estimated_requests || '按时长'
})
const preflightConcurrency = computed(() => {
  if (!preflight.value) return '—'
  if (isComparison.value) return `${preflight.value.aggregate_load.max_concurrency} 并发`
  return `${preflight.value.estimated_max_concurrency} 并发`
})
const rulerSegments = computed(() => {
  if (form.plan.plan_type === 'stepped') return form.plan.stages.map(stage => ({ name: stage.name, detail: `${stage.concurrency} 并发 · ${stage.requests} 请求`, weight: stage.requests || 1, kind: 'load' }))
  const segments = []
  if (form.plan.warmup_seconds) segments.push({ name: '预热', detail: `${form.plan.warmup_seconds}s`, weight: Math.max(1, form.plan.warmup_seconds), kind: 'warmup' })
  segments.push({ name: '负载', detail: loadDetail.value, weight: 6, kind: 'load' })
  if (form.plan.cooldown_seconds) segments.push({ name: '冷却', detail: `${form.plan.cooldown_seconds}s`, weight: Math.max(1, form.plan.cooldown_seconds), kind: 'cooldown' })
  return segments
})
const loadDetail = computed(() => form.plan.plan_type === 'constant_rate' ? `${form.plan.target_qps || 0} QPS` : form.plan.plan_type === 'stability' ? `${form.plan.duration_seconds || 0}s 稳定性` : `${form.plan.concurrency} 并发 · ${form.plan.total_requests} 请求`)
const preflightFingerprint = computed(() => JSON.stringify({
  execution_mode: form.execution_mode,
  api_config_id: form.api_config_id,
  targets: form.comparison.targets.map(target => target.api_config_id),
  comparison_mode: form.comparison.mode,
  plan: form.plan,
  workload: form.workload,
  risk_confirmed: form.risk_confirmed
}))

watch(preflightFingerprint, () => { preflight.value = null; notice.value = null })
watch(() => form.comparison.mode, mode => { if (mode === 'sequential') form.comparison.confirm_synchronous = false })
onMounted(async () => {
  try {
    [configs.value, datasets.value] = await Promise.all([api.configs(), api.datasets()])
    const preferred = configs.value.find(item => item.is_default) || configs.value[0]
    if (preferred) form.api_config_id = preferred.id
    form.comparison.targets.forEach((target, index) => { target.api_config_id = configs.value[index]?.id || '' })
  } catch (error) { notice.value = { tone: 'danger', title: '资源加载失败', message: error.message } }
})

function configById(id) { return configs.value.find(item => item.id === id) }
function availableTargetConfigs(currentId) {
  return configs.value.filter(item => item.id === currentId || !selectedTargetIds.value.includes(item.id))
}
function addComparisonTarget() {
  const next = configs.value.find(item => !selectedTargetIds.value.includes(item.id))
  form.comparison.targets.push({ key: `target-${++targetSequence}`, api_config_id: next?.id || '' })
}
function removeComparisonTarget(index) {
  if (form.comparison.targets.length > 2) form.comparison.targets.splice(index, 1)
}
function formatNumber(value) { return Number.isInteger(value) ? String(value) : Number(value).toFixed(1) }
function normalisePlan() {
  if (form.plan.plan_type === 'stepped' && !form.plan.stages.length) form.plan.stages = [
    { name: '探测', concurrency: 1, requests: 10 }, { name: '工作区', concurrency: 4, requests: 40 }, { name: '上探', concurrency: 8, requests: 60 }
  ]
  if (form.plan.plan_type === 'constant_rate') { form.plan.target_qps = form.plan.target_qps || 5; form.plan.duration_seconds = form.plan.duration_seconds || 30 }
  if (form.plan.plan_type === 'stability') form.plan.duration_seconds = form.plan.duration_seconds || 300
}
function addStage() { form.plan.stages.push({ name: `阶段 ${form.plan.stages.length + 1}`, concurrency: 1, requests: 10 }) }
function removeStage(index) { if (form.plan.stages.length > 1) form.plan.stages.splice(index, 1) }
function commonPayload() {
  return {
    name: form.name,
    plan: { ...form.plan },
    workload: { ...form.workload, dataset_id: form.workload.dataset_id || undefined },
    stream: form.stream,
    formats: form.formats,
    risk_confirmed: form.risk_confirmed
  }
}
function singlePayload() { return { ...commonPayload(), api_config_id: form.api_config_id } }
function comparisonPayload() {
  return {
    ...commonPayload(),
    mode: form.comparison.mode,
    resource_semantics: form.comparison.resource_semantics,
    confirm_synchronous: form.comparison.confirm_synchronous,
    targets: form.comparison.targets.map(target => {
      const config = configById(target.api_config_id)
      return { name: config ? `${config.name} · ${config.model}` : target.key, api_config_id: target.api_config_id }
    })
  }
}
async function runPreflight() {
  if (!canConfigure.value) return
  busy.value = true; notice.value = null; needsConfirmation.value = false
  try {
    preflight.value = isComparison.value ? await api.comparisonPreflight(comparisonPayload()) : await api.preflight(singlePayload())
    const risks = preflight.value.risks || []
    notice.value = { tone: risks.length ? 'warning' : 'success', title: '预检通过', message: risks.length ? `已确认风险：${risks.join('、')}` : isComparison.value ? '每模型与总生成负载均在本机安全上限内。' : '计划在本机安全上限内，可以加入队列。' }
  } catch (error) {
    if (error.status === 409) needsConfirmation.value = true
    notice.value = { tone: error.status === 409 ? 'warning' : 'danger', title: error.status === 409 ? '需要风险确认' : '预检未通过', message: error.message }
  } finally { busy.value = false }
}
async function submitTask() {
  if (!preflight.value || !canSubmit.value) return
  busy.value = true
  try {
    if (isComparison.value) {
      const comparison = await api.createComparison(comparisonPayload())
      router.push({ path: '/history', query: { comparison: comparison.id } })
    } else {
      const task = await api.createTask(singlePayload())
      router.push(`/runs/${task.id}`)
    }
  } catch (error) { notice.value = { tone: 'danger', title: isComparison.value ? '模型比较未创建' : '任务未创建', message: error.message } }
  finally { busy.value = false }
}
</script>
