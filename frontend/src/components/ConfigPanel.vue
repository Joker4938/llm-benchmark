<template>
  <el-card class="config-panel" shadow="never">
    <div slot="header">⚙️ 参数配置</div>

    <el-form label-position="top" size="small">
      <el-divider content-position="left">API 接口设置</el-divider>
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
  </el-card>
</template>

<script>
const STORAGE_KEY = 'llm-benchmark-config'

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
        llmUrl: saved.llmUrl || 'http://localhost:8000/v1',
        apiKey: saved.apiKey || 'default',
        model: saved.model || 'deepseek-r1',
        numRequests: saved.numRequests || 50,
        concurrency: saved.concurrency || 10,
        outputTokens: saved.outputTokens || 100,
        timeout: saved.timeout || 60,
        useLongContext: saved.useLongContext || false
      }
    }
  },
  watch: {
    config: {
      deep: true,
      handler(val) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(val))
      }
    }
  },
  methods: {
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
