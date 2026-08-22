<template>
  <AppShell
    eyebrow="OFFLINE ACCEPTANCE"
    title="浏览器验收记录"
    description="在目标 Windows 7 浏览器中执行自动探针与关键流程检查，证据只导出到本地。"
  >
    <template #actions>
      <div class="head-actions">
        <button class="secondary-button" type="button" :disabled="probing" @click="runAutomaticChecks">
          {{ probing ? '正在检查…' : '重新执行自动检查' }}
        </button>
        <button class="primary-button" type="button" @click="exportEvidence">
          导出 JSON 证据
        </button>
      </div>
    </template>

    <section class="acceptance-status panel" :class="`is-${overallStatus}`" aria-live="polite">
      <div class="acceptance-stamp">
        <span>ACCEPTANCE</span>
        <strong>{{ statusLabel }}</strong>
      </div>
      <div class="acceptance-progress">
        <p>已记录 {{ completedCount }} / {{ totalCount }} 项</p>
        <div class="acceptance-progress-track" aria-hidden="true">
          <span :style="{ width: progressPercent + '%' }"></span>
        </div>
        <small>{{ statusDescription }}</small>
      </div>
      <dl class="acceptance-summary">
        <div><dt>自动探针</dt><dd>{{ automaticSummary }}</dd></div>
        <div><dt>人工流程</dt><dd>{{ manualSummary }}</dd></div>
        <div><dt>数据边界</dt><dd>仅本地导出</dd></div>
      </dl>
    </section>

    <StateNotice
      v-if="probeError"
      tone="danger"
      title="部分自动探针未通过"
      :message="probeError"
    />

    <div class="acceptance-layout">
      <section class="panel acceptance-target-panel">
        <div class="panel-heading">
          <span>TARGET</span><h2>目标机信息</h2><small>由验收人员确认</small>
        </div>
        <div class="target-browser-options" role="radiogroup" aria-label="目标浏览器">
          <label v-for="option in targetOptions" :key="option.value" :class="{ active: form.target === option.value }">
            <input v-model="form.target" type="radio" :value="option.value">
            <span><strong>{{ option.title }}</strong><small>{{ option.note }}</small></span>
          </label>
        </div>
        <div class="field-row">
          <label>验收人员<input v-model.trim="form.operator" placeholder="姓名或工号，不填也可导出"></label>
          <label>实际浏览器版本<input v-model.trim="form.browserVersion" placeholder="例如 109.0.5414.120"></label>
        </div>
        <label>实际系统版本<input v-model.trim="form.osVersion" placeholder="例如 Windows 7 SP1 64 位"></label>
        <div class="environment-ticket">
          <p><span>USER AGENT</span><code>{{ environment.userAgent }}</code></p>
          <dl>
            <div><dt>平台</dt><dd>{{ environment.platform }}</dd></div>
            <div><dt>语言</dt><dd>{{ environment.language }}</dd></div>
            <div><dt>视口</dt><dd>{{ environment.viewport }}</dd></div>
            <div><dt>屏幕</dt><dd>{{ environment.screen }}</dd></div>
            <div><dt>像素比</dt><dd>{{ environment.pixelRatio }}</dd></div>
            <div>
              <dt>页面地址</dt><dd class="mono">
                {{ environment.origin }}
              </dd>
            </div>
          </dl>
        </div>
        <p class="form-help">
          页面不会读取密码、Cookie、API Key、模型响应或报告内容；导出前可直接打开 JSON 复核。
        </p>
      </section>

      <section class="panel acceptance-probe-panel">
        <div class="panel-heading">
          <span>AUTO PROBE</span><h2>能力与服务探针</h2><small>{{ automaticPassed }}/{{ automaticChecks.length }} 通过</small>
        </div>
        <div class="probe-group">
          <h3>浏览器能力</h3>
          <ul class="acceptance-check-list">
            <li v-for="item in capabilities" :key="item.id">
              <span class="probe-state" :class="item.ok ? 'pass' : 'fail'">{{ item.ok ? '通过' : '失败' }}</span>
              <div><strong>{{ item.name }}</strong><small>{{ item.note }}</small></div>
              <code>{{ item.value }}</code>
            </li>
          </ul>
        </div>
        <div class="probe-group">
          <h3>同源服务</h3>
          <ul class="acceptance-check-list">
            <li v-for="item in endpointChecks" :key="item.id">
              <span class="probe-state" :class="item.status">{{ probeStatusLabel(item.status) }}</span>
              <div><strong>{{ item.name }}</strong><small>{{ item.path }}</small></div>
              <code>{{ item.detail }}</code>
            </li>
          </ul>
        </div>
      </section>
    </div>

    <section class="panel acceptance-manual-panel">
      <div class="panel-heading">
        <span>MANUAL FLOW</span><h2>关键流程验收</h2><small>逐项标记，不自动推定</small>
      </div>
      <div class="manual-check-grid">
        <article v-for="(item, index) in manualChecks" :key="item.id" :class="`is-${item.status}`">
          <div class="manual-check-index">
            {{ String(index + 1).padStart(2, '0') }}
          </div>
          <div class="manual-check-copy">
            <h3>{{ item.title }}</h3>
            <p>{{ item.detail }}</p>
          </div>
          <div class="manual-check-actions" :aria-label="`${item.title}结果`">
            <button type="button" :class="{ active: item.status === 'pass' }" @click="item.status = 'pass'">
              通过
            </button>
            <button type="button" :class="{ active: item.status === 'fail' }" @click="item.status = 'fail'">
              失败
            </button>
            <button type="button" :class="{ active: item.status === 'pending' }" @click="item.status = 'pending'">
              待验
            </button>
          </div>
        </article>
      </div>
      <label class="acceptance-notes">验收备注<textarea v-model.trim="form.notes" rows="5" placeholder="记录异常现象、复现步骤、截图文件名或其他补充信息。不要粘贴密码和 API Key。"></textarea></label>
    </section>

    <section class="acceptance-footnote">
      <strong>判定规则</strong>
      <p>自动探针和人工流程全部通过时，页面显示“建议通过”；任何失败项均显示“存在问题”。最终是否完成 OpenSpec 8.9，仍需分别在真实 Windows 7 Chrome 109 与 Firefox 115 ESR 上保存证据后人工确认。</p>
    </section>
  </AppShell>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import AppShell from '../components/AppShell.vue'
import StateNotice from '../components/StateNotice.vue'

const targetOptions = [
  { value: 'windows-7-chrome-109', title: 'Chrome 109', note: 'Windows 7 最终支持版本' },
  { value: 'windows-7-firefox-115-esr', title: 'Firefox 115 ESR', note: 'Windows 7 ESR 验收版本' },
  { value: 'other', title: '辅助环境', note: '只做回归，不计入实机验收' }
]

const form = reactive({
  target: detectTarget(),
  operator: '',
  browserVersion: detectBrowserVersion(),
  osVersion: detectOperatingSystem(),
  notes: ''
})
const probing = ref(false)
const probeError = ref('')
const viewport = reactive({ width: window.innerWidth, height: window.innerHeight })
const endpointChecks = reactive([
  { id: 'live', name: '存活检查', path: '/health/live', status: 'pending', detail: '等待检查' },
  { id: 'ready', name: '就绪检查', path: '/health/ready', status: 'pending', detail: '等待检查' },
  { id: 'session', name: '登录会话', path: '/api/auth/verify', status: 'pending', detail: '等待检查' }
])
const manualChecks = reactive([
  { id: 'login', title: '登录与会话刷新', detail: '登录页无白屏或错位，登录成功，刷新页面后会话仍有效。', status: 'pending' },
  { id: 'workbench', title: '工作台与预检', detail: '配置压测计划并执行预检，错误提示和风险提示可读。', status: 'pending' },
  { id: 'single-task', title: '默认单任务队列', detail: '已有任务运行时，新任务进入等待或被明确阻止并给出原因。', status: 'pending' },
  { id: 'comparison', title: '同步模型比较确认', detail: '启用同步比较前必须显式确认资源叠加风险。', status: 'pending' },
  { id: 'run-monitor', title: '运行监控', detail: '实时指标、阶段、取消操作和异常状态均可正常查看。', status: 'pending' },
  { id: 'history', title: '历史与详情', detail: '历史筛选、任务详情、阈值结论和模型比较均可访问。', status: 'pending' },
  { id: 'resources', title: '测试资源', detail: '配置、数据集、计划和阈值的主要操作可用。', status: 'pending' },
  { id: 'settings', title: '设置与诊断', detail: '本机诊断可加载，工作台偏好可保存。', status: 'pending' },
  { id: 'event-fallback', title: '事件重连与轮询回退', detail: 'SSE 中断后能够重连；不可用时状态轮询仍可继续。', status: 'pending' },
  { id: 'viewport', title: '1366 × 768 布局', detail: '主要按钮、表格、弹窗和滚动区域无不可达内容。', status: 'pending' },
  { id: 'console-network', title: '控制台与网络边界', detail: '无语法错误、无公网资源请求、无凭据或敏感信息泄露。', status: 'pending' }
])

const capabilities = computed(() => [
  capability('promise', 'Promise', typeof Promise !== 'undefined', '异步流程基础能力'),
  capability('fetch', 'Fetch', typeof window.fetch === 'function', '管理 API 使用同源 Fetch 请求'),
  capability('event-source', 'EventSource', 'EventSource' in window, '实时事件不可用时应用回退轮询'),
  capability('url', 'URL', 'URL' in window, '用于下载与地址处理'),
  capability('abort-controller', 'AbortController', 'AbortController' in window, '用于请求取消和超时控制'),
  capability('text-encoder', 'TextEncoder', 'TextEncoder' in window, '用于现代文本编码能力'),
  capability('download', '文件下载', 'download' in document.createElement('a'), '本地导出报告和验收证据'),
  capability('media-query', 'Media Query', 'matchMedia' in window, '适配小屏和减少动态效果'),
  capability('css-grid', 'CSS Grid', supportsCss('display', 'grid'), '工作台主要栅格布局'),
  capability('sticky', 'Sticky Position', supportsCss('position', 'sticky'), '预检面板滚动定位')
])
const environment = computed(() => ({
  userAgent: navigator.userAgent || '未知',
  platform: navigator.platform || '未知',
  language: navigator.language || '未知',
  viewport: `${viewport.width} × ${viewport.height}`,
  screen: `${window.screen.width} × ${window.screen.height}`,
  pixelRatio: window.devicePixelRatio || 1,
  origin: window.location.origin
}))
const automaticChecks = computed(() => [
  ...capabilities.value.map(item => ({ status: item.ok ? 'pass' : 'fail' })),
  ...endpointChecks
])
const automaticPassed = computed(() => automaticChecks.value.filter(item => item.status === 'pass').length)
const automaticComplete = computed(() => automaticChecks.value.filter(item => item.status !== 'pending').length)
const manualPassed = computed(() => manualChecks.filter(item => item.status === 'pass').length)
const manualComplete = computed(() => manualChecks.filter(item => item.status !== 'pending').length)
const totalCount = computed(() => automaticChecks.value.length + manualChecks.length)
const completedCount = computed(() => automaticComplete.value + manualComplete.value)
const progressPercent = computed(() => totalCount.value ? Math.round(completedCount.value / totalCount.value * 100) : 0)
const hasFailure = computed(() => automaticChecks.value.some(item => item.status === 'fail') || manualChecks.some(item => item.status === 'fail'))
const allPassed = computed(() => automaticPassed.value === automaticChecks.value.length && manualPassed.value === manualChecks.length)
const overallStatus = computed(() => allPassed.value ? 'pass' : hasFailure.value ? 'fail' : 'pending')
const statusLabel = computed(() => ({ pass: '建议通过', fail: '存在问题', pending: '等待完成' }[overallStatus.value]))
const statusDescription = computed(() => {
  if (overallStatus.value === 'pass') return '本次记录全部通过，请核对目标机信息并导出证据。'
  if (overallStatus.value === 'fail') return '至少一项失败，请在备注中记录现象和复现步骤。'
  return '自动探针运行后，仍需验收人员逐项执行关键流程。'
})
const automaticSummary = computed(() => `${automaticPassed.value}/${automaticChecks.value.length} 通过`)
const manualSummary = computed(() => `${manualPassed.value}/${manualChecks.length} 通过`)

function capability(id, name, ok, note) {
  return { id, name, ok, note, value: ok ? 'supported' : 'unavailable' }
}

function supportsCss(property, value) {
  return Boolean(window.CSS && typeof window.CSS.supports === 'function' && window.CSS.supports(property, value))
}

function detectTarget() {
  const agent = navigator.userAgent || ''
  if (!/Windows NT 6\.1/.test(agent)) return 'other'
  if (/Firefox\/115(?:\.|\s|$)/.test(agent)) return 'windows-7-firefox-115-esr'
  if (/Chrome\/109(?:\.|\s|$)/.test(agent)) return 'windows-7-chrome-109'
  return 'other'
}

function detectBrowserVersion() {
  const agent = navigator.userAgent || ''
  const firefox = agent.match(/Firefox\/([\d.]+)/)
  if (firefox) return `Firefox ${firefox[1]}`
  const chrome = agent.match(/Chrome\/([\d.]+)/)
  if (chrome) return `Chrome ${chrome[1]}`
  return '未自动识别'
}

function detectOperatingSystem() {
  const agent = navigator.userAgent || ''
  if (/Windows NT 6\.1/.test(agent)) return 'Windows 7'
  const windows = agent.match(/Windows NT ([\d.]+)/)
  if (windows) return `Windows NT ${windows[1]}`
  const mac = agent.match(/Mac OS X ([\d_]+)/)
  if (mac) return `macOS ${mac[1].replace(/_/g, '.')}`
  if (/Linux/.test(agent)) return 'Linux'
  return navigator.platform || '未自动识别'
}

function probeStatusLabel(status) {
  return { pass: '通过', fail: '失败', pending: '检查中' }[status]
}

async function probeEndpoint(item) {
  item.status = 'pending'
  item.detail = '请求中'
  try {
    const response = await fetch(item.path, { credentials: 'same-origin', cache: 'no-store' })
    item.status = response.ok ? 'pass' : 'fail'
    item.detail = `HTTP ${response.status}`
  } catch (reason) {
    item.status = 'fail'
    item.detail = reason && reason.message ? reason.message : '网络请求失败'
  }
}

async function runAutomaticChecks() {
  probing.value = true
  probeError.value = ''
  await Promise.all(endpointChecks.map(item => probeEndpoint(item)))
  const failed = automaticChecks.value.filter(item => item.status === 'fail')
  if (failed.length) probeError.value = `有 ${failed.length} 项自动检查失败，请确认浏览器能力、登录会话和本机服务状态。`
  probing.value = false
}

function buildEvidence() {
  return {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    product: 'llm-benchmark',
    acceptance_scope: 'windows-7-browser-client',
    target: {
      id: form.target,
      operator: form.operator || null,
      browser_version: form.browserVersion || null,
      os_version: form.osVersion || null
    },
    environment: environment.value,
    automatic_checks: [
      ...capabilities.value.map(item => ({ id: item.id, name: item.name, status: item.ok ? 'pass' : 'fail', detail: item.note, value: item.value })),
      ...endpointChecks.map(item => ({ id: item.id, name: item.name, path: item.path, status: item.status, detail: item.detail }))
    ],
    manual_checks: manualChecks.map(item => ({ id: item.id, title: item.title, status: item.status, detail: item.detail })),
    summary: {
      status: overallStatus.value,
      automatic_passed: automaticPassed.value,
      automatic_total: automaticChecks.value.length,
      manual_passed: manualPassed.value,
      manual_total: manualChecks.length
    },
    notes: form.notes || null,
    privacy: 'No password, cookie, API key, model response or report content is collected.'
  }
}

function exportEvidence() {
  const content = JSON.stringify(buildEvidence(), null, 2)
  const blob = new Blob([content], { type: 'application/json;charset=utf-8' })
  const link = document.createElement('a')
  const date = new Date().toISOString().slice(0, 10)
  const target = form.target.replace(/[^a-z0-9-]/gi, '-')
  link.href = URL.createObjectURL(blob)
  link.download = `browser-acceptance-${target}-${date}.json`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.setTimeout(() => URL.revokeObjectURL(link.href), 0)
}

function updateViewport() {
  viewport.width = window.innerWidth
  viewport.height = window.innerHeight
}

onMounted(() => {
  window.addEventListener('resize', updateViewport)
  runAutomaticChecks()
})

onBeforeUnmount(() => window.removeEventListener('resize', updateViewport))
</script>
