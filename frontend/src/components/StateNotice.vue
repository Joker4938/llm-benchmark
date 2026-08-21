<template>
  <div class="state-notice" :class="tone" role="status">
    <strong>{{ title }}</strong><span>{{ message }}</span>
  </div>
</template>
<script setup>
defineProps({
  title: { type: String, required: true },
  message: { type: String, required: true },
  tone: { type: String, default: 'info' }
})
</script>
