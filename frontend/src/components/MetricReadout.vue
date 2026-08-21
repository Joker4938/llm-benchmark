<template>
  <div class="metric-readout" :class="tone">
    <span>{{ label }}</span>
    <strong>{{ displayValue }}</strong>
    <small>{{ unit || hint }}</small>
  </div>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({
  label: { type: String, required: true },
  value: { type: [String, Number], default: null },
  unit: { type: String, default: '' },
  hint: { type: String, default: 'N/A' },
  tone: { type: String, default: '' }
})
const displayValue = computed(() => props.value === null || props.value === undefined ? '—' : props.value)
</script>
