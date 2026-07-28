import type { Router } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { checkLoginStatus } from '@/api/auth'

/** 路由守卫: 未登录跳登录页, 未初始化跳 Setup 页 */
export function setupGuard(router: Router) {
  router.beforeEach(async (to) => {
    const auth = useAuthStore()

    if (to.meta.public) {
      // 已登录用户访问登录页时直接进入后台
      if (auth.token && (to.name === 'login' || to.name === 'setup')) {
        return { name: 'dashboard' }
      }
      return true
    }

    if (!auth.token) {
      try {
        const { initialized } = await checkLoginStatus()
        return initialized ? { name: 'login' } : { name: 'setup' }
      } catch {
        return { name: 'login' }
      }
    }
    return true
  })
}
