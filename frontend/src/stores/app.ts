import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

/** UI 状态: 侧边栏折叠、暗色模式 (localStorage 持久化) */
export const useAppStore = defineStore('app', () => {
  const collapsed = ref(localStorage.getItem('sider-collapsed') === '1')
  const dark = ref(localStorage.getItem('theme-dark') === '1')

  watch(collapsed, (v) => localStorage.setItem('sider-collapsed', v ? '1' : '0'))
  watch(dark, (v) => localStorage.setItem('theme-dark', v ? '1' : '0'))

  function toggleSider() {
    collapsed.value = !collapsed.value
  }

  function toggleDark() {
    dark.value = !dark.value
  }

  return { collapsed, dark, toggleSider, toggleDark }
})
