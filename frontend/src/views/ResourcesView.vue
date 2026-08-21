<template>
  <AppShell title="测试资源" description="把连接、数据、计划与阈值保存成可复现的本地资源。">
    <div class="resource-tabs" role="tablist">
      <button v-for="item in tabs" :key="item.id" type="button" role="tab" :aria-selected="tab === item.id" :class="{ active: tab === item.id }" @click="tab = item.id">
        {{ item.label }}<span>{{ counts[item.id] }}</span>
      </button>
    </div>
    <StateNotice v-if="notice" :tone="notice.tone" :title="notice.title" :message="notice.message" />

    <div v-if="tab === 'configs'" class="resource-layout">
      <section class="resource-list panel">
        <div class="panel-heading">
          <span>ENDPOINTS</span><h2>API 配置</h2>
        </div>
        <article v-for="item in configs" :key="item.id" class="resource-item">
          <div><strong>{{ item.name }}</strong><span>{{ item.model }}</span><small>{{ item.base_url }}</small></div><code>{{ item.api_key }}</code><button type="button" class="icon-button" aria-label="删除配置" @click="remove('config', item)">
            ×
          </button>
        </article>
        <p v-if="!configs.length" class="inline-empty">
          还没有连接配置。保存后 API Key 会以安装级密钥加密。
        </p>
      </section>
      <form class="panel resource-form" @submit.prevent="saveConfig">
        <div class="panel-heading">
          <span>NEW ENDPOINT</span><h2>保存连接</h2>
        </div>
        <label>配置名称<input v-model.trim="configForm.name" required></label><label>Base URL<input v-model.trim="configForm.base_url" placeholder="http://model-server/v1" required></label><label>模型名称<input v-model.trim="configForm.model" required></label><label>API Key<input v-model="configForm.api_key" type="password" autocomplete="new-password"><small>保存后仅显示掩码；留空表示服务端不需要 Key。</small></label>
        <div class="field-row">
          <label>超时（秒）<input v-model.number="configForm.timeout_seconds" type="number" min="1" max="3600"></label><label class="toggle-label"><input v-model="configForm.verify_tls" type="checkbox"><span>校验 TLS 证书</span></label>
        </div><label class="toggle-label"><input v-model="configForm.is_default" type="checkbox"><span>设为默认连接</span></label><button class="primary-button" type="submit">
          加密保存连接
        </button>
      </form>
    </div>

    <div v-else-if="tab === 'datasets'" class="resource-layout">
      <section class="resource-list panel">
        <div class="panel-heading">
          <span>DATASETS</span><h2>消息数据集</h2>
        </div>
        <article v-for="item in datasets" :key="item.id" class="resource-item">
          <div><strong>{{ item.name }}</strong><span>版本 {{ item.version }}</span><small class="mono">SHA {{ item.sha256.slice(0, 16) }}</small></div><button type="button" class="icon-button" aria-label="删除数据集" @click="remove('dataset', item)">
            ×
          </button>
        </article>
        <p v-if="!datasets.length" class="inline-empty">
          支持 OpenAI messages JSONL，每行一条可复现请求。
        </p>
      </section>
      <form class="panel resource-form" @submit.prevent="saveDataset">
        <div class="panel-heading">
          <span>JSONL</span><h2>导入数据集</h2>
        </div>
        <label>数据集名称<input v-model.trim="datasetForm.name" required></label><label>JSONL 内容<textarea v-model="datasetForm.content_jsonl" rows="14" spellcheck="false" required></textarea></label><p class="form-help mono">
          {"messages":[{"role":"user","content":"解释一致性哈希"}]}
        </p><button class="primary-button" type="submit">
          校验并保存
        </button>
      </form>
    </div>

    <div v-else-if="tab === 'plans'" class="resource-layout">
      <section class="resource-list panel">
        <div class="panel-heading">
          <span>PLANS</span><h2>计划模板</h2>
        </div>
        <article v-for="item in plans" :key="item.id" class="resource-item">
          <div><strong>{{ item.name }}</strong><span>{{ item.plan.plan_type }}</span><small>{{ item.plan.concurrency || 1 }} 并发</small></div><button type="button" class="icon-button" aria-label="删除计划" @click="remove('plan', item)">
            ×
          </button>
        </article>
        <p v-if="!plans.length" class="inline-empty">
          保存常用计划，减少重复输入并统一测试口径。
        </p>
      </section>
      <form class="panel resource-form" @submit.prevent="savePlan">
        <div class="panel-heading">
          <span>TEMPLATE</span><h2>保存固定并发模板</h2>
        </div>
        <label>模板名称<input v-model.trim="planForm.name" required></label><div class="field-row">
          <label>并发<input v-model.number="planForm.concurrency" type="number" min="1" max="500"></label><label>请求数<input v-model.number="planForm.total_requests" type="number" min="1"></label>
        </div><div class="field-row">
          <label>预热（秒）<input v-model.number="planForm.warmup_seconds" type="number" min="0"></label><label>冷却（秒）<input v-model.number="planForm.cooldown_seconds" type="number" min="0"></label>
        </div><button class="primary-button" type="submit">
          保存计划模板
        </button>
      </form>
    </div>

    <div v-else class="resource-layout">
      <section class="resource-list panel">
        <div class="panel-heading">
          <span>THRESHOLDS</span><h2>阈值模板</h2>
        </div>
        <article v-for="item in thresholds" :key="item.id" class="resource-item">
          <div><strong>{{ item.name }}</strong><span>{{ item.rules.length }} 条规则</span><small>{{ item.rules.map(rule => `${rule.metric} ${rule.operator} ${rule.value}`).join('；') }}</small></div><button type="button" class="icon-button" aria-label="删除阈值" @click="remove('threshold', item)">
            ×
          </button>
        </article>
        <p v-if="!thresholds.length" class="inline-empty">
          阈值逐项给出通过、失败或不可评估，不生成含糊总分。
        </p>
      </section>
      <form class="panel resource-form" @submit.prevent="saveThreshold">
        <div class="panel-heading">
          <span>QUALITY GATE</span><h2>保存阈值</h2>
        </div>
        <label>模板名称<input v-model.trim="thresholdForm.name" required></label><div class="field-row three">
          <label>指标<select v-model="thresholdForm.metric"><option value="latency.p95">P95 延迟</option><option value="ttft.p95">TTFT P95</option><option value="error_rate">错误率</option><option value="achieved_qps">实际 QPS</option><option value="aggregate_output_tps">聚合 TPS</option></select></label><label>关系<select v-model="thresholdForm.operator"><option>&lt;=</option><option>&gt;=</option><option>&lt;</option><option>&gt;</option></select></label><label>阈值<input v-model.number="thresholdForm.value" type="number" step="0.01" required></label>
        </div><button class="primary-button" type="submit">
          保存阈值模板
        </button>
      </form>
    </div>
  </AppShell>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api/client'
import AppShell from '../components/AppShell.vue'
import StateNotice from '../components/StateNotice.vue'

const tab = ref('configs'); const configs = ref([]); const datasets = ref([]); const plans = ref([]); const thresholds = ref([]); const notice = ref(null)
const tabs = [{ id: 'configs', label: 'API 配置' }, { id: 'datasets', label: '数据集' }, { id: 'plans', label: '计划模板' }, { id: 'thresholds', label: '阈值模板' }]
const counts = computed(() => ({ configs: configs.value.length, datasets: datasets.value.length, plans: plans.value.length, thresholds: thresholds.value.length }))
const configForm = reactive({ name: '', base_url: '', model: '', api_key: '', verify_tls: true, timeout_seconds: 60, is_default: false })
const datasetForm = reactive({ name: '', content_jsonl: '{"messages":[{"role":"user","content":"请用三点说明该模型的能力边界"}]}' })
const planForm = reactive({ name: '', concurrency: 4, total_requests: 40, warmup_seconds: 2, cooldown_seconds: 1 })
const thresholdForm = reactive({ name: '', metric: 'latency.p95', operator: '<=', value: 2 })

onMounted(load)
async function load() {
  try { [configs.value, datasets.value, plans.value, thresholds.value] = await Promise.all([api.configs(), api.datasets(), api.plans(), api.thresholds()]) }
  catch (error) { showError(error) }
}
async function saveConfig() { try { await api.createConfig(configForm); Object.assign(configForm, { name: '', base_url: '', model: '', api_key: '', verify_tls: true, timeout_seconds: 60, is_default: false }); await load(); showSuccess('连接已加密保存') } catch (error) { showError(error) } }
async function saveDataset() { try { await api.createDataset(datasetForm); datasetForm.name = ''; await load(); showSuccess('数据集已校验并保存') } catch (error) { showError(error) } }
async function savePlan() { try { await api.createPlan({ name: planForm.name, plan: { name: planForm.name, plan_type: 'fixed_concurrency', concurrency: planForm.concurrency, total_requests: planForm.total_requests, warmup_seconds: planForm.warmup_seconds, cooldown_seconds: planForm.cooldown_seconds } }); planForm.name = ''; await load(); showSuccess('计划模板已保存') } catch (error) { showError(error) } }
async function saveThreshold() { try { await api.createThreshold({ name: thresholdForm.name, rules: [{ metric: thresholdForm.metric, operator: thresholdForm.operator, value: thresholdForm.value }] }); thresholdForm.name = ''; await load(); showSuccess('阈值模板已保存') } catch (error) { showError(error) } }
async function remove(type, item) {
  if (!window.confirm(`确认删除“${item.name}”？`)) return
  try {
    if (type === 'config') await api.deleteConfig(item.id)
    if (type === 'dataset') await api.deleteDataset(item.id)
    if (type === 'plan') await api.deletePlan(item.id)
    if (type === 'threshold') await api.deleteThreshold(item.id)
    await load(); showSuccess('资源已删除')
  } catch (error) { showError(error) }
}
function showSuccess(message) { notice.value = { tone: 'success', title: '操作完成', message } }
function showError(error) { notice.value = { tone: 'danger', title: '操作失败', message: error.message } }
</script>
