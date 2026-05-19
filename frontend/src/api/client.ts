import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api'

export const apiClient = axios.create({
  baseURL: API_BASE,
  timeout: 5000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 健康检查
export async function checkHealth(): Promise<boolean> {
  try {
    await axios.get('/health', { timeout: 3000 })
    return true
  } catch {
    return false
  }
}
