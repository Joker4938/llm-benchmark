const API_BASE = ''

export class ApiError extends Error {
  constructor(message, status, details) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.details = details
  }
}

export async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) }
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json'
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: 'same-origin',
    cache: 'no-store',
    ...options,
    headers,
    body: options.body && typeof options.body !== 'string' ? JSON.stringify(options.body) : options.body
  })
  if (response.status === 204) return null
  const contentType = response.headers.get('content-type') || ''
  const data = contentType.includes('application/json') ? await response.json() : await response.text()
  if (!response.ok) {
    const error = data && data.error
    throw new ApiError(error && error.message ? error.message : `请求失败（${response.status}）`, response.status, error && error.details)
  }
  return data
}

export const api = {
  login: credentials => request('/api/auth/login', { method: 'POST', body: credentials }),
  verify: () => request('/api/auth/verify'),
  logout: () => request('/api/auth/logout', { method: 'POST' }),
  configs: () => request('/api/configs'),
  createConfig: value => request('/api/configs', { method: 'POST', body: value }),
  updateConfig: (id, value) => request(`/api/configs/${id}`, { method: 'PUT', body: value }),
  deleteConfig: id => request(`/api/configs/${id}`, { method: 'DELETE' }),
  datasets: () => request('/api/datasets'),
  createDataset: value => request('/api/datasets', { method: 'POST', body: value }),
  deleteDataset: id => request(`/api/datasets/${id}`, { method: 'DELETE' }),
  plans: () => request('/api/plans'),
  createPlan: value => request('/api/plans', { method: 'POST', body: value }),
  deletePlan: id => request(`/api/plans/${id}`, { method: 'DELETE' }),
  thresholds: () => request('/api/thresholds'),
  createThreshold: value => request('/api/thresholds', { method: 'POST', body: value }),
  deleteThreshold: id => request(`/api/thresholds/${id}`, { method: 'DELETE' }),
  preflight: value => request('/api/plans/preflight', { method: 'POST', body: value }),
  createTask: value => request('/api/tasks', { method: 'POST', body: value }),
  tasks: () => request('/api/tasks?limit=500'),
  task: id => request(`/api/tasks/${id}`),
  taskStatus: id => request(`/api/tasks/${id}/status`),
  cancelTask: id => request(`/api/tasks/${id}/cancel`, { method: 'POST' }),
  baselineCandidates: id => request(`/api/tasks/${id}/baseline-candidates`),
  savedBaseline: id => request(`/api/tasks/${id}/baseline`),
  previewBaseline: (id, baselineId) => request(`/api/tasks/${id}/baseline/${baselineId}`),
  saveBaseline: (id, value) => request(`/api/tasks/${id}/baseline`, { method: 'PUT', body: value }),
  deleteBaseline: id => request(`/api/tasks/${id}/baseline`, { method: 'DELETE' }),
  reports: taskId => request(`/api/reports${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ''}`),
  generateReport: (id, format) => request(`/api/tasks/${id}/reports`, { method: 'POST', body: { format } }),
  deleteReport: id => request(`/api/reports/${id}`, { method: 'DELETE' }),
  diagnostics: () => request('/api/diagnostics'),
  settings: () => request('/api/settings'),
  saveSettings: value => request('/api/settings', { method: 'PUT', body: value })
}

export function reportDownloadUrl(id) {
  return `${API_BASE}/api/reports/${encodeURIComponent(id)}/download`
}
