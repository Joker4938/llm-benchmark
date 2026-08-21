<template>
  <main class="login-page">
    <section class="login-instrument" aria-labelledby="login-title">
      <div class="login-signal" aria-hidden="true">
        <span v-for="height in bars" :key="height" :style="{ height: `${height}%` }"></span>
      </div>
      <div class="login-copy">
        <p class="eyebrow">
          OFFLINE / SINGLE NODE
        </p>
        <h1 id="login-title">
          把模型服务<br>放上测试台。
        </h1>
        <p>连接、加载、测量、复查。所有配置和报告只保存在这台内网服务器。</p>
        <dl>
          <div><dt>协议</dt><dd>OpenAI Chat Completions</dd></div>
          <div><dt>运行</dt><dd>完全离线</dd></div>
          <div><dt>队列</dt><dd>默认单任务</dd></div>
        </dl>
      </div>
      <form class="login-panel" @submit.prevent="submit">
        <div class="panel-index">
          ACCESS / 01
        </div>
        <h2>进入精密测试台</h2>
        <p>此登录仅用于阻止内网中的无关操作。</p>
        <label>共享账户<input v-model.trim="form.username" autocomplete="username" required autofocus></label>
        <label>密码<input v-model="form.password" type="password" autocomplete="current-password" required></label>
        <p v-if="error" class="field-error" role="alert">
          {{ error }}
        </p>
        <button class="primary-button full" type="submit" :disabled="loading">
          {{ loading ? '正在校验…' : '进入测试台' }}
        </button>
      </form>
    </section>
  </main>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSessionStore } from '../stores/session'

const form = reactive({ username: '', password: '' })
const loading = ref(false)
const error = ref('')
const bars = [22, 41, 35, 68, 47, 78, 54, 91, 62, 46, 72, 38, 58, 30]
const route = useRoute()
const router = useRouter()
const session = useSessionStore()

async function submit() {
  loading.value = true
  error.value = ''
  try {
    await session.login(form)
    router.replace(typeof route.query.next === 'string' ? route.query.next : '/')
  } catch (reason) {
    error.value = reason.message || '登录失败，请检查配置。'
  } finally {
    loading.value = false
  }
}
</script>
