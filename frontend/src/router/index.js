import { createRouter, createWebHashHistory } from 'vue-router'
import { useSessionStore } from '../stores/session'
import LoginView from '../views/LoginView.vue'
import WorkbenchView from '../views/WorkbenchView.vue'
import RunView from '../views/RunView.vue'
import HistoryView from '../views/HistoryView.vue'
import ResourcesView from '../views/ResourcesView.vue'
import SettingsView from '../views/SettingsView.vue'
import BrowserAcceptanceView from '../views/BrowserAcceptanceView.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/login', name: 'login', component: LoginView, meta: { public: true } },
    { path: '/', name: 'workbench', component: WorkbenchView },
    { path: '/runs/:id?', name: 'run', component: RunView },
    { path: '/history', name: 'history', component: HistoryView },
    { path: '/resources', name: 'resources', component: ResourcesView },
    { path: '/settings', name: 'settings', component: SettingsView },
    { path: '/browser-acceptance', name: 'browser-acceptance', component: BrowserAcceptanceView },
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
})

router.beforeEach(async to => {
  const session = useSessionStore()
  if (!session.checked) await session.verify()
  if (!to.meta.public && !session.user) return { name: 'login', query: { next: to.fullPath } }
  if (to.name === 'login' && session.user) return { name: 'workbench' }
  return true
})

export default router
