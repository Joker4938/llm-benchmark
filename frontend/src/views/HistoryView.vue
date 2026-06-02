<template>
  <div class="history-view">
    <h2>📚 历史报告</h2>
    <el-table :data="reports" size="small" border stripe v-loading="loading">
      <el-table-column prop="filename" label="文件名" min-width="200" />
      <el-table-column prop="size" label="大小" width="120" />
      <el-table-column prop="mtime" label="生成时间" width="180" />
      <el-table-column label="操作" width="180">
        <template slot-scope="scope">
          <el-button size="mini" type="primary" @click="downloadReport(scope.row.filename)">下载</el-button>
          <el-button size="mini" type="danger" @click="deleteReport(scope.row.filename)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && reports.length === 0" description="暂无历史报告" />
  </div>
</template>

<script>
import api from '@/api'

export default {
  name: 'HistoryView',
  data() {
    return { reports: [], loading: false }
  },
  mounted() {
    this.fetchReports()
  },
  methods: {
    async fetchReports() {
      this.loading = true
      try {
        const res = await api.get('/reports')
        this.reports = res.data
      } catch (err) {
        this.$message.error('获取报告列表失败')
      } finally {
        this.loading = false
      }
    },
    async downloadReport(filename) {
      try {
        const res = await api.get(`/reports/download/${encodeURIComponent(filename)}`, { responseType: 'blob' })
        const url = window.URL.createObjectURL(new Blob([res.data]))
        const link = document.createElement('a')
        link.href = url
        link.setAttribute('download', filename)
        document.body.appendChild(link)
        link.click()
        link.remove()
      } catch (err) {
        this.$message.error('下载失败')
      }
    },
    async deleteReport(filename) {
      try {
        await this.$confirm(`确定删除 ${filename} 吗？`, '提示', { type: 'warning' })
        await api.delete(`/reports/${encodeURIComponent(filename)}`)
        this.$message.success('删除成功')
        this.fetchReports()
      } catch (err) {
        if (err !== 'cancel') {
          this.$message.error('删除失败')
        }
      }
    }
  }
}
</script>

<style scoped>
.history-view { padding: 10px; }
</style>
