<template>
  <el-card class="config-panel" shadow="never">
    <div slot="header">⚙️ 参数配置</div>

    <el-form label-position="top" size="small">
      <el-divider content-position="left">API 接口设置</el-divider>

      <!-- API Config Preset Selector -->
      <el-form-item>
        <div style="display: flex; gap: 8px; width: 100%">
          <el-select
            v-model="selectedConfigId"
            placeholder="选择已保存的 API 配置"
            style="flex: 1"
            :loading="configsLoading"
            clearable
            @change="onConfigSelect"
          >
            <el-option
              v-for="c in apiConfigs"
              :key="c.id"
              :label="c.name + (c.is_default ? ' ⭐' : '')"
              :value="c.id"
            />
            <div v-if="apiConfigs.length === 0" style="padding: 8px 0; text-align: center; color: #909399; font-size: 12px">
              暂无已保存的配置，请先添加
            </div>
          </el-select>
          <el-button
            type="primary"
            plain
            size="small"
            :disabled="!config.llmUrl || !config.model"
            @click="saveConfig"
          >
            <i class="el-icon-upload2" /> 保存
          </el-button>
          <el-button size="small" @click="showManageDialog = true">
            <i class="el-icon-setting" /> 管理
          </el-button>
        </div>
      </el-form-item>

      <el-form-item label="API 接口地址 (Base URL)">
        <el-input v-model="config.llmUrl" placeholder="http://localhost:8000/v1" />
      </el-form-item>
      <el-form-item label="API 密钥 (API Key)">
        <el-input v-model="config.apiKey" placeholder="default" show-password />
      </el-form-item>
      <el-form-item label="模型名称 (Model)">
        <el-input v-model="config.model" placeholder="deepseek-r1" />
      </el-form-item>

      <el-divider content-position="left">快捷压测模板</el-divider>
      <el-form-item>
        <el-select v-model="preset" placeholder="选择模板" style="width: 100%" @change="onPresetChange">
          <el-option v-for="(item, key) in presets" :key="key" :label="key" :value="key" />
        </el-select>
      </el-form-item>

      <div v-if="preset === '自动化多级梯度测试 (Auto-Gradient)'">
        <el-alert type="info" :closable="false">
          系统将自动运行多个并发档位 (1, 50, 100, 200, 300) 并生成压测性能曲线
        </el-alert>
        <el-form-item label="使用长文本上下文提示词" style="margin-top: 12px">
          <el-checkbox v-model="config.useLongContext">启用</el-checkbox>
        </el-form-item>
      </div>

      <div v-else>
        <el-divider content-position="left">压测参数设置</el-divider>
        <el-form-item label="总请求数">
          <el-input-number v-model="config.numRequests" :min="1" :step="10" style="width: 100%" />
        </el-form-item>
        <el-form-item label="并发数">
          <el-input-number v-model="config.concurrency" :min="1" :max="1000" :step="1" style="width: 100%" />
        </el-form-item>
        <el-form-item label="最大输出 Token 数">
          <el-input-number v-model="config.outputTokens" :min="1" :step="50" style="width: 100%" />
        </el-form-item>
        <el-form-item label="请求超时时间 (秒)">
          <el-input-number v-model="config.timeout" :min="1" :step="10" style="width: 100%" />
        </el-form-item>
        <el-form-item label="使用长文本上下文提示词">
          <el-checkbox v-model="config.useLongContext">启用</el-checkbox>
        </el-form-item>
      </div>

      <el-button
        type="primary"
        style="width: 100%; margin-top: 10px"
        :loading="running"
        @click="$emit('start', preset === '自动化多级梯度测试 (Auto-Gradient)')"
      >
        {{ preset === '自动化多级梯度测试 (Auto-Gradient)' ? '▶️ 开始多级梯度自动化压测' : '▶️ 开始单次压力测试' }}
      </el-button>
    </el-form>

    <!-- Unsaved changes confirmation dialog -->
    <el-dialog title="提示" :visible.sync="showDirtyConfirm" width="400px" append-to-body>
      <span>当前 API 配置有未保存的修改，切换配置将丢弃这些修改。是否继续？</span>
      <span slot="footer">
        <el-button size="small" @click="showDirtyConfirm = false">取消</el-button>
        <el-button size="small" type="warning" @click="confirmSwitchConfig">丢弃并切换</el-button>
      </span>
    </el-dialog>

    <!-- Save config dialog (prompt for name) -->
    <el-dialog title="保存 API 配置" :visible.sync="showSaveDialog" width="420px" append-to-body>
      <el-form label-position="top" size="small">
        <el-form-item label="配置名称" required>
          <el-input v-model="saveForm.name" placeholder="如：测试环境、生产环境" />
        </el-form-item>
        <el-form-item label="API 接口地址">
          <el-input :value="config.llmUrl" disabled />
        </el-form-item>
        <el-form-item label="模型名称">
          <el-input :value="config.model" disabled />
        </el-form-item>
      </el-form>
      <span slot="footer">
        <el-button size="small" @click="showSaveDialog = false">取消</el-button>
        <el-button size="small" type="primary" :loading="saveLoading" @click="doSaveConfig">保存</el-button>
      </span>
    </el-dialog>

    <!-- Manage configs dialog -->
    <el-dialog title="管理 API 配置" :visible.sync="showManageDialog" width="720px" append-to-body>
      <el-table :data="apiConfigs" size="small" empty-text="暂无配置" style="width: 100%">
        <el-table-column prop="name" label="配置名称" min-width="120">
          <template slot-scope="{ row }">
            <span>{{ row.name }} <el-tag v-if="row.is_default" type="success" size="mini">默认</el-tag></span>
          </template>
        </el-table-column>
        <el-table-column prop="llm_url" label="API 地址" min-width="160" show-overflow-tooltip />
        <el-table-column prop="model" label="模型" min-width="100" show-overflow-tooltip />
        <el-table-column label="API Key" min-width="100" show-overflow-tooltip>
          <template slot-scope="{ row }">
            {{ row.api_key }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template slot-scope="{ row }">
            <el-button type="text" size="mini" @click="editConfig(row)">编辑</el-button>
            <el-button type="text" size="mini" :disabled="row.is_default" @click="setDefault(row)">设为默认</el-button>
            <el-button type="text" size="mini" style="color: #F56C6C" @click="confirmDeleteConfig(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <!-- Edit config sub-dialog -->
    <el-dialog title="编辑配置" :visible.sync="showEditDialog" width="420px" append-to-body>
      <el-form label-position="top" size="small">
        <el-form-item label="配置名称" required>
          <el-input v-model="editForm.name" />
        </el-form-item>
        <el-form-item label="API 接口地址" required>
          <el-input v-model="editForm.llm_url" />
        </el-form-item>
        <el-form-item label="API 密钥 (API Key)">
          <el-input v-model="editForm.api_key" show-password placeholder="留空保持不变" />
        </el-form-item>
        <el-form-item label="模型名称" required>
          <el-input v-model="editForm.model" />
        </el-form-item>
      </el-form>
      <span slot="footer">
        <el-button size="small" @click="showEditDialog = false">取消</el-button>
        <el-button size="small" type="primary" :loading="editLoading" @click="doEditConfig">保存</el-button>
      </span>
    </el-dialog>
  </el-card>
</template>

<script>
import { getConfigs, createConfig, updateConfig, deleteConfig, setDefaultConfig } from '@/api'

const STORAGE_KEY = 'llm-benchmark-config'
// Only these fields go to localStorage (benchmark params, not API connection)
const BENCHMARK_PARAMS = ['numRequests', 'concurrency', 'outputTokens', 'timeout', 'useLongContext']

export default {
  name: 'ConfigPanel',
  props: { running: { type: Boolean, default: false } },
  data() {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
    return {
      preset: '自定义 (Custom)',
      presets: {
        '自定义 (Custom)': { req: 50, conc: 10, tokens: 100, timeout: 60, long: false },
        '自动化多级梯度测试 (Auto-Gradient)': null,
        '🟢 快速体验 (低负载/短文本)': { req: 20, conc: 2, tokens: 50, timeout: 30, long: false },
        '🟡 中等负载 (常规并发)': { req: 100, conc: 30, tokens: 150, timeout: 60, long: false },
        '🔴 极限压测 (高并发)': { req: 500, conc: 150, tokens: 100, timeout: 120, long: false },
        '📚 长文本推理测试': { req: 50, conc: 10, tokens: 250, timeout: 120, long: true },
      },
      config: {
        llmUrl: 'http://localhost:8000/v1',
        apiKey: 'default',
        model: 'deepseek-r1',
        numRequests: saved.numRequests || 50,
        concurrency: saved.concurrency || 10,
        outputTokens: saved.outputTokens || 100,
        timeout: saved.timeout || 60,
        useLongContext: saved.useLongContext || false
      },
      // API config presets
      apiConfigs: [],
      selectedConfigId: null,
      previousSelectedConfigId: null,
      configsLoading: false,
      // Dirty tracking snapshot for API fields
      apiFieldsSnapshot: { llmUrl: '', apiKey: '', model: '' },
      // Dialogs
      showDirtyConfirm: false,
      pendingConfigId: null,
      showSaveDialog: false,
      saveForm: { name: '' },
      saveLoading: false,
      showManageDialog: false,
      showEditDialog: false,
      editForm: { id: '', name: '', llm_url: '', api_key: '', model: '' },
      editLoading: false
    }
  },
  watch: {
    // Only save benchmark params to localStorage (not API connection fields)
    config: {
      deep: true,
      handler(val) {
        const toSave = {}
        BENCHMARK_PARAMS.forEach(k => { toSave[k] = val[k] })
        localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave))
      }
    }
  },
  async mounted() {
    await this.loadConfigs()
  },
  methods: {
    async loadConfigs() {
      this.configsLoading = true
      try {
        const res = await getConfigs()
        this.apiConfigs = res.data || []
        // Auto-select default config if no config currently selected
        if (!this.selectedConfigId) {
          const def = this.apiConfigs.find(c => c.is_default)
          if (def) {
            this.applyConfig(def)
            this.selectedConfigId = def.id
            this.previousSelectedConfigId = def.id
          }
        }
      } catch (e) {
        console.error('加载 API 配置失败', e)
      } finally {
        this.configsLoading = false
      }
    },

    applyConfig(cfg) {
      this.config.llmUrl = cfg.llm_url
      this.config.apiKey = cfg.api_key
      this.config.model = cfg.model
      this.takeApiSnapshot()
    },

    takeApiSnapshot() {
      this.apiFieldsSnapshot = {
        llmUrl: this.config.llmUrl,
        apiKey: this.config.apiKey,
        model: this.config.model
      }
    },

    isApiFieldsDirty() {
      return (
        this.config.llmUrl !== this.apiFieldsSnapshot.llmUrl ||
        this.config.apiKey !== this.apiFieldsSnapshot.apiKey ||
        this.config.model !== this.apiFieldsSnapshot.model
      )
    },

    onConfigSelect(id) {
      if (!id) {
        this.takeApiSnapshot()
        return
      }
      if (this.isApiFieldsDirty()) {
        this.pendingConfigId = id
        this.showDirtyConfirm = true
        // Revert el-select to previous selection
        this.$nextTick(() => {
          this.selectedConfigId = this.previousSelectedConfigId || null
        })
        return
      }
      const cfg = this.apiConfigs.find(c => c.id === id)
      if (cfg) {
        this.applyConfig(cfg)
        this.previousSelectedConfigId = id
      }
    },

    confirmSwitchConfig() {
      const id = this.pendingConfigId
      this.showDirtyConfirm = false
      this.pendingConfigId = null
      this.selectedConfigId = id
      this.previousSelectedConfigId = id
      const cfg = this.apiConfigs.find(c => c.id === id)
      if (cfg) {
        this.applyConfig(cfg)
      }
    },

    async saveConfig() {
      if (this.selectedConfigId) {
        // Update existing selected config
        try {
          await updateConfig(this.selectedConfigId, {
            llm_url: this.config.llmUrl,
            api_key: this.config.apiKey,
            model: this.config.model
          })
          this.$message.success('配置已更新')
          this.takeApiSnapshot()
          await this.loadConfigs()
        } catch (e) {
          this.$message.error(e.response?.data?.message || '更新失败')
        }
      } else {
        // Save as new — prompt for name
        this.saveForm.name = ''
        this.showSaveDialog = true
      }
    },

    async doSaveConfig() {
      const name = (this.saveForm.name || '').trim()
      if (!name) {
        this.$message.warning('请输入配置名称')
        return
      }
      this.saveLoading = true
      try {
        const res = await createConfig({
          name,
          llm_url: this.config.llmUrl,
          api_key: this.config.apiKey,
          model: this.config.model
        })
        this.$message.success('配置已保存')
        this.showSaveDialog = false
        this.selectedConfigId = res.data.id
        this.takeApiSnapshot()
        await this.loadConfigs()
      } catch (e) {
        this.$message.error(e.response?.data?.message || '保存失败')
      } finally {
        this.saveLoading = false
      }
    },

    editConfig(row) {
      this.editForm = {
        id: row.id,
        name: row.name,
        llm_url: row.llm_url,
        api_key: '',  // don't show masked key; leave empty to keep unchanged
        model: row.model
      }
      this.showEditDialog = true
    },

    async doEditConfig() {
      const f = this.editForm
      if (!f.name.trim()) {
        this.$message.warning('配置名称不能为空')
        return
      }
      if (!f.llm_url.trim()) {
        this.$message.warning('API 接口地址不能为空')
        return
      }
      if (!f.model.trim()) {
        this.$message.warning('模型名称不能为空')
        return
      }
      this.editLoading = true
      try {
        const payload = { name: f.name, llm_url: f.llm_url, model: f.model }
        if (f.api_key) {
          payload.api_key = f.api_key
        }
        await updateConfig(f.id, payload)
        this.$message.success('配置已更新')
        this.showEditDialog = false
        // If editing the currently selected config, refresh form
        if (f.id === this.selectedConfigId) {
          const res = await getConfigs()
          const updated = (res.data || []).find(c => c.id === f.id)
          if (updated) {
            // Re-fetch without masking by updating the form fields from edit
            this.config.llmUrl = f.llm_url
            if (f.api_key) this.config.apiKey = f.api_key
            this.config.model = f.model
            this.takeApiSnapshot()
          }
        }
        await this.loadConfigs()
      } catch (e) {
        this.$message.error(e.response?.data?.message || '更新失败')
      } finally {
        this.editLoading = false
      }
    },

    confirmDeleteConfig(row) {
      this.$confirm(
        `确定要删除配置 "${row.name}" 吗？${row.is_default ? '（当前默认配置，删除后将自动设置下一个为默认）' : ''}`,
        '删除确认',
        { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
      ).then(async () => {
        try {
          await deleteConfig(row.id)
          this.$message.success('已删除')
          if (this.selectedConfigId === row.id) {
            this.selectedConfigId = null
          }
          await this.loadConfigs()
        } catch (e) {
          this.$message.error(e.response?.data?.message || '删除失败')
        }
      }).catch(() => {})
    },

    async setDefault(row) {
      try {
        await setDefaultConfig(row.id)
        this.$message.success(`"${row.name}" 已设为默认`)
        await this.loadConfigs()
      } catch (e) {
        this.$message.error(e.response?.data?.message || '操作失败')
      }
    },

    onPresetChange(val) {
      const p = this.presets[val]
      if (p) {
        this.config.numRequests = p.req
        this.config.concurrency = p.conc
        this.config.outputTokens = p.tokens
        this.config.timeout = p.timeout
        this.config.useLongContext = p.long
      }
    },
    getConfig() {
      return { ...this.config }
    }
  }
}
</script>

<style scoped>
.config-panel { height: 100%; overflow-y: auto; }
</style>
