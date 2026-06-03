import axios from 'axios'

const api = axios.create({
  baseURL: process.env.NODE_ENV === 'development' ? '/api' : './api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' }
})

api.interceptors.request.use(config => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  response => response,
  error => {
    if (error.response && error.response.status === 401) {
      localStorage.removeItem('token')
      window.location.hash = '#/login'
    }
    return Promise.reject(error)
  }
)

// Config preset API methods
export function getConfigs() {
  return api.get('/configs')
}

export function createConfig(data) {
  return api.post('/configs', data)
}

export function updateConfig(id, data) {
  return api.put(`/configs/${id}`, data)
}

export function deleteConfig(id) {
  return api.delete(`/configs/${id}`)
}

export function setDefaultConfig(id) {
  return api.post(`/configs/${id}/default`)
}

export default api
