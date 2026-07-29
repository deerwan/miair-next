import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

/** 主题模式: 亮色 / 暗色 / 跟随系统 (青龙式三态, 默认跟随系统) */
export type ThemeMode = 'light' | 'dark' | 'auto'

const THEME_KEY = 'theme-mode'

function readTheme(): ThemeMode {
  const v = localStorage.getItem(THEME_KEY)
  return v === 'light' || v === 'dark' ? v : 'auto'
}

/** UI 状态: 侧边栏折叠、主题模式 (localStorage 持久化) */
export const useAppStore = defineStore('app', () => {
  const collapsed = ref(localStorage.getItem('sider-collapsed') === '1')
  const theme = ref<ThemeMode>(readTheme())

  // 跟随系统: 监听系统深色偏好变化实时生效
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  const systemDark = ref(media.matches)
  media.addEventListener('change', (e) => {
    systemDark.value = e.matches
  })

  const dark = computed(() =>
    theme.value === 'auto' ? systemDark.value : theme.value === 'dark',
  )

  watch(collapsed, (v) => localStorage.setItem('sider-collapsed', v ? '1' : '0'))
  watch(theme, (v) => localStorage.setItem(THEME_KEY, v))

  function toggleSider() {
    collapsed.value = !collapsed.value
  }

  return { collapsed, theme, dark, toggleSider }
})
