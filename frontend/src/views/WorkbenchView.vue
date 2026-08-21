<template>
  <AppShell title="新建性能实验" description="先定义可复现的负载，再让测试台发出请求。">
    <template #actions>
      <div class="head-actions">
        <span class="mode-chip">{{ form.plan.plan_type === 'stepped' ? '阶梯负载' : '单段负载' }}</span>
      </div>
    </template>

    <LoadRuler :segments="rulerSegments" :progress="0" label="计划负载轨迹" />

    <form class="bench-grid" @submit.prevent="submitTask">
      <section class="panel config-panel">
        <div class="panel-heading">
          <span>CONNECTION</span><h2>连接与模型</h2>
        </div>
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
          <span>预计请求</span><strong>{{ preflight ? (preflight.estimated_requests || '按时长') : '待预检' }}</strong>
          <small>最大并发 {{ preflight ? preflight.estimated_max_concurrency : '—' }}</small>
        </div>
        <StateNotice v-if="notice" :tone="notice.tone" :title="notice.title" :message="notice.message" />
        <label v-if="needsConfirmation" class="risk-confirm"><input v-model="form.risk_confirmed" type="checkbox"><span>我已确认高并发 / 高 QPS / 长时间运行会叠加内网服务负载</span></label>
        <div class="submit-stack">
          <button type="button" class="secondary-button" :disabled="busy" @click="runPreflight">
            执行安全预检
          </button>
          <button class="primary-button" type="submit" :disabled="busy || !preflight">
            {{ busy ? '正在处理…' : '加入本地任务队列' }}
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
const availableFormats = [
  { value: 'json', label: 'JSON 摘要' }, { value: 'jsonl.gz', label: '样本 JSONL' },
  { value: 'events.jsonl.gz', label: '时间序列' }, { value: 'html', label: '离线 HTML' },
  { value: 'xlsx', label: 'XLSX' }, { value: 'csv', label: 'CSV' }
]
const form = reactive({
  name: `性能实验-${new Date().toISOString().slice(0, 16).replace('T', '-')}`,
  api_config_id: '',
  plan: { plan_type: 'fixed_concurrency', concurrency: 4, total_requests: 40, warmup_seconds: 2, cooldown_seconds: 1, stages: [] },
  workload: { output_size: 128, dataset_id: '' },
  stream: true,
  formats: ['json', 'jsonl.gz', 'events.jsonl.gz', 'html'],
  risk_confirmed: false
})
const selectedConfig = computed(() => configs.value.find(item => item.id === form.api_config_id))
const rulerSegments = computed(() => {
  if (form.plan.plan_type === 'stepped') return form.plan.stages.map(stage => ({ name: stage.name, detail: `${stage.concurrency} 并发 · ${stage.requests} 请求`, weight: stage.requests || 1, kind: 'load' }))
  const segments = []
  if (form.plan.warmup_seconds) segments.push({ name: '预热', detail: `${form.plan.warmup_seconds}s`, weight: Math.max(1, form.plan.warmup_seconds), kind: 'warmup' })
  segments.push({ name: '负载', detail: loadDetail.value, weight: 6, kind: 'load' })
  if (form.plan.cooldown_seconds) segments.push({ name: '冷却', detail: `${form.plan.cooldown_seconds}s`, weight: Math.max(1, form.plan.cooldown_seconds), kind: 'cooldown' })
  return segments
})
const loadDetail = computed(() => form.plan.plan_type === 'constant_rate' ? `${form.plan.target_qps || 0} QPS` : form.plan.plan_type === 'stability' ? `${form.plan.duration_seconds || 0}s 稳定性` : `${form.plan.concurrency} 并发 · ${form.plan.total_requests} 请求`)

watch(() => JSON.stringify(form), () => { preflight.value = null; notice.value = null }, { deep: false })
onMounted(async () => {
  try {
    [configs.value, datasets.value] = await Promise.all([api.configs(), api.datasets()])
    const preferred = configs.value.find(item => item.is_default) || configs.value[0]
    if (preferred) form.api_config_id = preferred.id
  } catch (error) { notice.value = { tone: 'danger', title: '资源加载失败', message: error.message } }
})

function normalisePlan() {
  if (form.plan.plan_type === 'stepped' && !form.plan.stages.length) form.plan.stages = [
    { name: '探测', concurrency: 1, requests: 10 }, { name: '工作区', concurrency: 4, requests: 40 }, { name: '上探', concurrency: 8, requests: 60 }
  ]
  if (form.plan.plan_type === 'constant_rate') { form.plan.target_qps = form.plan.target_qps || 5; form.plan.duration_seconds = form.plan.duration_seconds || 30 }
  if (form.plan.plan_type === 'stability') form.plan.duration_seconds = form.plan.duration_seconds || 300
}
function addStage() { form.plan.stages.push({ name: `阶段 ${form.plan.stages.length + 1}`, concurrency: 1, requests: 10 }) }
function removeStage(index) { if (form.plan.stages.length > 1) form.plan.stages.splice(index, 1) }
function payload() {
  return {
    name: form.name, api_config_id: form.api_config_id, plan: { ...form.plan },
    workload: { ...form.workload, dataset_id: form.workload.dataset_id || undefined },
    stream: form.stream, formats: form.formats, risk_confirmed: form.risk_confirmed
  }
}
async function runPreflight() {
  busy.value = true; notice.value = null; needsConfirmation.value = false
  try {
    preflight.value = await api.preflight(payload())
    notice.value = { tone: preflight.value.risks.length ? 'warning' : 'success', title: '预检通过', message: preflight.value.risks.length ? `已确认风险：${preflight.value.risks.join('、')}` : '计划在本机安全上限内，可以加入队列。' }
  } catch (error) {
    if (error.status === 409) needsConfirmation.value = true
    notice.value = { tone: error.status === 409 ? 'warning' : 'danger', title: error.status === 409 ? '需要风险确认' : '预检未通过', message: error.message }
  } finally { busy.value = false }
}
async function submitTask() {
  if (!preflight.value) return
  busy.value = true
  try {
    const task = await api.createTask(payload())
    router.push(`/runs/${task.id}`)
  } catch (error) { notice.value = { tone: 'danger', title: '任务未创建', message: error.message } }
  finally { busy.value = false }
}
</script>
