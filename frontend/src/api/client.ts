import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api'

export const apiClient = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 请求重试拦截器：遇到 5xx 或网络错误时自动重试
apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error.config
    if (!config || config._retryCount !== undefined) {
      return Promise.reject(error)
    }
    // 仅对 5xx 服务器错误和 ECONNABORTED（超时）重试
    const isRetryable =
      (error.response?.status ?? 0) >= 500 ||
      error.code === 'ECONNABORTED' ||
      error.code === 'ERR_NETWORK'

    if (!isRetryable) {
      return Promise.reject(error)
    }

    config._retryCount = (config._retryCount || 0) + 1
    const delay = Math.min(config._retryCount * 2000, 10000)
    console.warn(`[API] 请求失败({config._retryCount}次)，${delay}ms 后重试...`)
    await new Promise((r) => setTimeout(r, delay))
    return apiClient(config)
  }
)

// 健康检查
export async function checkHealth(): Promise<boolean> {
  try {
    await axios.get('/health', { timeout: 3000 })
    return true
  } catch {
    return false
  }
}
