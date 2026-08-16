import { defineStore } from 'pinia'
import { ref } from 'vue'
import { fetchMe } from '@/api/auth'

/** 登录状态: token 存取 (localStorage 持久化) */
export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem('access-token') || '')
  const username = ref<string>('')

  function setToken(t: string) {
    token.value = t
    localStorage.setItem('access-token', t)
  }

  /** 从后端 /me 拉取真实用户名，避免前端写死/信任本地输入框文本 */
  async function refreshUser() {
    try {
      const { username: name } = await fetchMe()
      username.value = name
    } catch {
      username.value = ''
    }
  }

  function logout() {
    token.value = ''
    username.value = ''
    localStorage.removeItem('access-token')
  }

  return { token, username, setToken, refreshUser, logout }
})
