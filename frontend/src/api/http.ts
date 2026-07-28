import axios from 'axios'
import { useAuthStore } from '@/stores/auth'
import router from '@/router'

const http = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
})

// 自动携带 token
http.interceptors.request.use((config) => {
  const auth = useAuthStore()
  if (auth.token) {
    config.headers.Authorization = `Bearer ${auth.token}`
  }
  return config
})

// 401 统一踢回登录页
http.interceptors.response.use(
  (resp) => resp,
  (error) => {
    if (error.response?.status === 401) {
      const auth = useAuthStore()
      auth.logout()
      if (router.currentRoute.value.name !== 'login') {
        router.push({ name: 'login' })
      }
    }
    return Promise.reject(error)
  },
)

export default http
