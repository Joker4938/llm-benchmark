import { defineStore } from 'pinia'
import { api } from '../api/client'

export const useSessionStore = defineStore('session', {
  state: () => ({ user: null, expiresAt: null, checked: false }),
  actions: {
    async verify() {
      try {
        const data = await api.verify()
        this.user = data.user
        this.expiresAt = data.expires_at
        return true
      } catch (error) {
        this.user = null
        this.expiresAt = null
        return false
      } finally {
        this.checked = true
      }
    },
    async login(credentials) {
      const data = await api.login(credentials)
      this.user = data.user
      this.expiresAt = data.expires_at
    },
    async logout() {
      try { await api.logout() } finally {
        this.user = null
        this.expiresAt = null
      }
    }
  }
})
