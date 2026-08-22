<template>
  <AppShell title="设置与诊断" description="检查本地执行器、数据目录和浏览器回退能力，不进行公网探测。">
    <template #actions>
      <button class="secondary-button" type="button" :disabled="loading" @click="loadDiagnostics">
        重新诊断
      </button>
    </template>
    <StateNotice v-if="error" tone="danger" title="诊断失败" :message="error" />
    <div class="settings-grid">
      <section class="panel diagnostic-panel">
        <div class="panel-heading">
          <span>LOCAL HEALTH</span><h2>单机运行状态</h2>
        </div>
        <div class="diagnostic-gauge">
          <span :style="{ width: diskPercent + '%' }"></span>
        </div>
        <dl v-if="diagnostics" class="diagnostic-list">
          <div><dt>数据库</dt><dd>{{ diagnostics.database }}<span class="health-ok">正常</span></dd></div>
          <div><dt>数据目录剩余</dt><dd>{{ formatBytes(diagnostics.disk.free) }} / {{ formatBytes(diagnostics.disk.total) }}</dd></div>
          <div><dt>Executor 心跳</dt><dd>{{ executorStatus }}</dd></div>
          <div><dt>生成器 CPU</dt><dd>{{ diagnostics.generator.cpu_count || '未知' }} 核</dd></div>
          <div>
            <dt>Python</dt><dd class="mono">
              {{ diagnostics.python }}
            </dd>
          </div>
          <div><dt>旧数据迁移</dt><dd>{{ migrationSummary }}</dd></div>
          <div><dt>公网检查</dt><dd>已跳过（完全离线）</dd></div>
        </dl>
        <div v-else class="inline-empty">
          正在读取本机诊断信息…
        </div>
      </section>
      <form class="panel settings-panel" @submit.prevent="save">
        <div class="panel-heading">
          <span>DISPLAY</span><h2>工作台偏好</h2>
        </div>
        <label>默认记录刷新间隔（秒）<input v-model.number="settings.poll_seconds" type="number" min="1" max="30"></label>
        <label>默认时间窗口（秒）<input v-model.number="settings.window_seconds" type="number" min="1" max="60"></label>
        <label class="toggle-label"><input v-model="settings.compact_numbers" type="checkbox"><span>大数值使用紧凑显示</span></label>
        <label class="toggle-label"><input v-model="settings.confirm_delete" type="checkbox"><span>删除报告前再次确认</span></label>
        <button class="primary-button" type="submit">
          保存本地偏好
        </button>
        <StateNotice v-if="saved" tone="success" title="设置已保存" message="偏好已写入本机数据库。" />
      </form>
      <section class="panel compatibility-panel">
        <div class="panel-heading">
          <span>BROWSER</span><h2>浏览器能力</h2>
        </div>
        <ul class="capability-list">
          <li v-for="item in capabilities" :key="item.name">
            <span :class="item.ok ? 'health-ok' : 'health-warning'">{{ item.ok ? '可用' : '回退' }}</span><strong>{{ item.name }}</strong><small>{{ item.note }}</small>
          </li>
        </ul>
        <p class="form-help">
          目标：Windows 7 上 Chrome 109 / Firefox 115 ESR。IE 不在支持范围内。
        </p>
        <RouterLink class="secondary-button compatibility-action" to="/browser-acceptance">
          打开浏览器验收记录
        </RouterLink>
      </section>
    </div>
  </AppShell>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api/client'
import AppShell from '../components/AppShell.vue'
import StateNotice from '../components/StateNotice.vue'

const diagnostics = ref(null); const loading = ref(false); const error = ref(''); const saved = ref(false)
const settings = reactive({ poll_seconds: 2, window_seconds: 5, compact_numbers: true, confirm_delete: true })
const capabilities = [
  { name: 'SSE 实时事件', ok: 'EventSource' in window, note: '不可用时自动切换到状态轮询' },
  { name: 'Fetch 请求', ok: 'fetch' in window, note: '用于全部管理 API' },
  { name: '本地文件下载', ok: 'download' in document.createElement('a'), note: '报告由服务端生成并下载' },
  { name: 'Reduced motion', ok: 'matchMedia' in window, note: '遵循系统减少动态效果设置' }
]
const diskPercent = computed(() => diagnostics.value ? Math.min(100, diagnostics.value.disk.used / diagnostics.value.disk.total * 100) : 0)
const executorStatus = computed(() => diagnostics.value && diagnostics.value.executor && diagnostics.value.executor.heartbeat_at ? `最近心跳 ${new Date(diagnostics.value.executor.heartbeat_at).toLocaleString('zh-CN', { hour12: false })}` : '尚未记录心跳')
const migrationSummary = computed(() => { const value = diagnostics.value && diagnostics.value.legacy_migration; return value ? `迁移 ${value.migrated} · 跳过 ${value.skipped} · 失败 ${value.failed}` : '无记录' })

onMounted(async () => { await Promise.all([loadDiagnostics(), loadSettings()]) })
async function loadDiagnostics() { loading.value = true; error.value = ''; try { diagnostics.value = await api.diagnostics() } catch (reason) { error.value = reason.message } finally { loading.value = false } }
async function loadSettings() { try { Object.assign(settings, await api.settings()) } catch (reason) { error.value = reason.message } }
async function save() { saved.value = false; try { Object.assign(settings, await api.saveSettings(settings)); saved.value = true } catch (reason) { error.value = reason.message } }
function formatBytes(value) { if (!value) return '0 B'; const units = ['B', 'KB', 'MB', 'GB', 'TB']; let number = value; let index = 0; while (number >= 1024 && index < units.length - 1) { number /= 1024; index += 1 } return `${number.toFixed(index ? 1 : 0)} ${units[index]}` }
</script>
