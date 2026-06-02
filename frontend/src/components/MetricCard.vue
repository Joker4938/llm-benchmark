<template>
  <el-card class="metric-card" shadow="hover">
    <div class="metric-label">{{ label }}</div>
    <div class="metric-value">{{ value }}</div>
    <div v-if="delta" class="metric-delta" :class="deltaColor">{{ delta }}</div>
  </el-card>
</template>

<script>
export default {
  name: 'MetricCard',
  props: {
    label: { type: String, required: true },
    value: { type: [String, Number], required: true },
    delta: { type: String, default: '' },
    deltaColor: { type: String, default: '' }
  }
}
</script>

<style scoped>
.metric-card {
  text-align: center;
  padding: 10px;
}
.metric-label {
  font-size: 14px;
  color: #606266;
  margin-bottom: 8px;
}
.metric-value {
  font-size: 28px;
  font-weight: bold;
  color: #303133;
}
.metric-delta {
  font-size: 12px;
  margin-top: 4px;
}
</style>
