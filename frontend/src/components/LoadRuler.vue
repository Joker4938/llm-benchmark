<template>
  <section class="load-ruler" :aria-label="label">
    <header>
      <div><span class="section-kicker">LOAD PROFILE</span><strong>{{ label }}</strong></div>
      <span class="mono">{{ progressLabel }}</span>
    </header>
    <div class="ruler-track" role="img" :aria-label="`${label}，进度 ${Math.round(progress * 100)}%`">
      <div
        v-for="(segment, index) in normalised"
        :key="`${segment.name}-${index}`"
        class="ruler-segment"
        :class="segment.kind"
        :style="{ flexGrow: segment.weight }"
        tabindex="0"
        :title="segmentTitle(segment)"
      >
        <span class="segment-fill" :style="{ width: fillWidth(index) }"></span>
        <span class="segment-label">{{ segment.name }}</span>
        <small>{{ segment.detail }}</small>
      </div>
      <span class="ruler-needle" :style="{ left: `${Math.min(100, Math.max(0, progress * 100))}%` }"></span>
    </div>
    <div class="ruler-scale" aria-hidden="true">
      <span>0</span><span>25</span><span>50</span><span>75</span><span>100%</span>
    </div>
  </section>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  label: { type: String, default: '负载轨迹' },
  progress: { type: Number, default: 0 },
  segments: { type: Array, default: () => [] }
})

const normalised = computed(() => props.segments.length ? props.segments : [
  { name: '预热', detail: '建立连接', weight: 1, kind: 'warmup' },
  { name: '负载', detail: '固定并发', weight: 5, kind: 'load' },
  { name: '冷却', detail: '排空请求', weight: 1, kind: 'cooldown' }
])
const progressLabel = computed(() => `${Math.round(Math.min(1, Math.max(0, props.progress)) * 100)} / 100`)

function fillWidth(index) {
  const total = normalised.value.reduce((sum, item) => sum + Number(item.weight || 1), 0)
  const before = normalised.value.slice(0, index).reduce((sum, item) => sum + Number(item.weight || 1), 0) / total
  const end = before + Number(normalised.value[index].weight || 1) / total
  if (props.progress <= before) return '0%'
  if (props.progress >= end) return '100%'
  return `${((props.progress - before) / (end - before)) * 100}%`
}

function segmentTitle(segment) {
  return `${segment.name}：${segment.detail || '未设置'}`
}
</script>
