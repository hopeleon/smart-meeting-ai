import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api'
const FORCE_MOCK = import.meta.env.VITE_MOCK_MODE === 'true'

export const apiClient = axios.create({
  baseURL: API_BASE,
  timeout: 5000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 自动检测后端是否可用
let _backendAvailable: boolean | null = null // null = 未检测
let _mockMode = FORCE_MOCK

export async function detectBackend(): Promise<boolean> {
  if (FORCE_MOCK) {
    _mockMode = true
    return false
  }
  try {
    await apiClient.get('/health', { timeout: 3000 })
    _backendAvailable = true
    _mockMode = false
    return true
  } catch {
    _backendAvailable = false
    _mockMode = true
    console.warn('[client] 后端不可用，自动降级到 Mock 模式')
    return false
  }
}

export function isMockMode(): boolean {
  return _mockMode
}

export function isBackendAvailable(): boolean {
  return _backendAvailable === true
}

// 启动时自动检测
if (!FORCE_MOCK) {
  detectBackend()
}
