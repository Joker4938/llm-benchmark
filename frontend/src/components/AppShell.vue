<template>
  <div class="shell">
    <aside class="rail" aria-label="主导航">
      <RouterLink class="brand" to="/" aria-label="LLM Benchmark 首页">
        <span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i></span>
        <span><strong>LLM BENCH</strong><small>精密测试台</small></span>
      </RouterLink>
      <nav class="rail-nav">
        <RouterLink v-for="item in navigation" :key="item.to" :to="item.to">
          <span class="nav-code">{{ item.code }}</span><span>{{ item.label }}</span>
        </RouterLink>
      </nav>
      <div class="rail-foot">
        <span class="status-dot"></span>
        <span>单机模式 · 离线</span>
        <button type="button" class="text-button" @click="signOut">
          退出
        </button>
      </div>
    </aside>
    <main class="workspace">
      <header class="workspace-head">
        <div>
          <p class="eyebrow">
            {{ eyebrow }}
          </p>
          <h1>{{ title }}</h1>
          <p v-if="description" class="page-description">
            {{ description }}
          </p>
        </div>
        <slot name="actions"></slot>
      </header>
      <slot></slot>
    </main>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { useSessionStore } from '../stores/session'

defineProps({
  eyebrow: { type: String, default: 'LLM BENCHMARK' },
  title: { type: String, required: true },
  description: { type: String, default: '' }
})

const router = useRouter()
const session = useSessionStore()
const navigation = [
  { to: '/', code: '01', label: '新建测试' },
  { to: '/runs', code: '02', label: '运行监控' },
  { to: '/history', code: '03', label: '测试记录' },
  { to: '/resources', code: '04', label: '测试资源' },
  { to: '/settings', code: '05', label: '设置诊断' },
  { to: '/browser-acceptance', code: '06', label: '浏览器验收' }
]

async function signOut() {
  await session.logout()
  router.push('/login')
}
</script>
