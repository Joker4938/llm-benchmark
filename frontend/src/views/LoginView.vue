<template>
  <div class="login-page">
    <el-card class="login-card" shadow="always">
      <div class="login-title">LLM 压测看板</div>
      <div class="login-subtitle">请登录以继续使用</div>
      <el-form :model="form" :rules="rules" ref="loginForm" @submit.native.prevent="handleLogin">
        <el-form-item prop="username">
          <el-input v-model="form.username" prefix-icon="el-icon-user" placeholder="用户名" />
        </el-form-item>
        <el-form-item prop="password">
          <el-input v-model="form.password" prefix-icon="el-icon-lock" placeholder="密码" show-password />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" class="login-btn" :loading="loading" @click="handleLogin">登 录</el-button>
        </el-form-item>
      </el-form>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    </el-card>
  </div>
</template>

<script>
import api from '@/api'

export default {
  name: 'LoginView',
  data() {
    return {
      form: { username: '', password: '' },
      rules: {
        username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
        password: [{ required: true, message: '请输入密码', trigger: 'blur' }]
      },
      loading: false,
      error: ''
    }
  },
  methods: {
    async handleLogin() {
      this.error = ''
      const valid = await this.$refs.loginForm.validate().catch(() => false)
      if (!valid) return
      this.loading = true
      try {
        const res = await api.post('/auth/login', this.form)
        localStorage.setItem('token', res.data.token)
        this.$router.push('/')
      } catch (err) {
        this.error = err.response?.data?.message || '登录失败'
      } finally {
        this.loading = false
      }
    }
  }
}
</script>

<style scoped>
.login-page {
  height: 100vh;
  display: flex;
  justify-content: center;
  align-items: center;
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
}
.login-card {
  width: 400px;
  padding: 20px;
}
.login-title {
  font-size: 24px;
  font-weight: bold;
  text-align: center;
  margin-bottom: 8px;
  color: #303133;
}
.login-subtitle {
  font-size: 14px;
  color: #909399;
  text-align: center;
  margin-bottom: 24px;
}
.login-btn {
  width: 100%;
}
</style>
