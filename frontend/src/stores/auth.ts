import { defineStore } from 'pinia'
import { ref } from 'vue'

/** 登录状态: token 存取 (localStorage 持久化) */
export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem('access-token') || '')
  const username = ref<string>('')

  function setToken(t: string) {
    token.value = t
    localStorage.setItem('access-token', t)
  }

  function logout() {
    token.value = ''
    username.value = ''
    localStorage.removeItem('access-token')
  }

  return { token, username, setToken, logout }
})
