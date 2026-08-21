import { defineStore } from 'pinia'
import { api } from '../api/client'

export const useTaskStore = defineStore('tasks', {
  state: () => ({ items: [], current: null, loading: false }),
  getters: {
    active: state => state.items.filter(item => ['queued', 'running', 'stopping'].includes(item.status))
  },
  actions: {
    async refresh() {
      this.loading = true
      try { this.items = await api.tasks() } finally { this.loading = false }
    },
    async load(id) {
      this.current = await api.task(id)
      const index = this.items.findIndex(item => item.id === id)
      if (index >= 0) this.items[index] = this.current
      return this.current
    }
  }
})
