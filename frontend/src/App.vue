<template>
  <div id="app">
    <el-container v-if="isLoggedIn">
      <el-header class="app-header">
        <div class="header-left">
          <span class="app-title">🚀 LLM 性能与高并发压力测试看板</span>
        </div>
        <div class="header-right">
          <el-menu
            :default-active="activeIndex"
            mode="horizontal"
            router
            background-color="#1a1a2e"
            text-color="#fff"
            active-text-color="#409EFF"
          >
            <el-menu-item index="/">压测</el-menu-item>
            <el-menu-item index="/history">历史报告</el-menu-item>
          </el-menu>
          <el-button type="text" class="logout-btn" @click="logout">退出</el-button>
        </div>
      </el-header>
      <el-main>
        <router-view />
      </el-main>
    </el-container>
    <router-view v-else />
  </div>
</template>

<script>
export default {
  name: 'App',
  computed: {
    isLoggedIn() {
      return !!localStorage.getItem('token')
    },
    activeIndex() {
      return this.$route.path
    }
  },
  methods: {
    logout() {
      localStorage.removeItem('token')
      this.$router.push('/login')
    }
  }
}
</script>

<style>
body {
  margin: 0;
  font-family: 'Helvetica Neue', Helvetica, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', Arial, sans-serif;
  background-color: #f0f2f5;
}
.app-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background-color: #1a1a2e;
  color: #fff;
  padding: 0 20px;
}
.app-title {
  font-size: 18px;
  font-weight: bold;
}
.header-right {
  display: flex;
  align-items: center;
}
.logout-btn {
  color: #fff !important;
  margin-left: 20px;
}
</style>
