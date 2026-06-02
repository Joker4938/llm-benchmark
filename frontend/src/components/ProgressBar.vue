<template>
  <div class="progress-container" v-if="visible">
    <el-progress :percentage="percentage" :status="status" :text-inside="true" :stroke-width="24"></el-progress>
    <div class="progress-text">{{ text }}</div>
  </div>
</template>

<script>
export default {
  name: 'ProgressBar',
  props: {
    visible: { type: Boolean, default: false },
    percentage: { type: Number, default: 0 },
    status: { type: String, default: '' },
    text: { type: String, default: '' }
  }
}
</script>

<style scoped>
.progress-container {
  margin: 20px 0;
}
.progress-text {
  text-align: center;
  margin-top: 8px;
  color: #606266;
  font-size: 14px;
}
</style>
