import { createRouter, createWebHistory } from 'vue-router'
import { setupGuard } from './guard'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/login/LoginView.vue'),
      meta: { public: true },
    },
    {
      path: '/setup',
      name: 'setup',
      component: () => import('@/views/login/SetupView.vue'),
      meta: { public: true },
    },
    {
      path: '/',
      component: () => import('@/layouts/AdminLayout.vue'),
      redirect: { name: 'dashboard' },
      children: [
        {
          path: 'dashboard',
          name: 'dashboard',
          component: () => import('@/views/dashboard/DashboardView.vue'),
          meta: { title: '总览' },
        },
        {
          path: 'devices',
          name: 'devices',
          component: () => import('@/views/devices/DevicesView.vue'),
          meta: { title: '设备管理' },
        },
        {
          path: 'account',
          name: 'account',
          component: () => import('@/views/account/AccountView.vue'),
          meta: { title: '账号配置' },
        },
        {
          path: 'playback',
          name: 'playback',
          component: () => import('@/views/playback/PlaybackView.vue'),
          meta: { title: '播放控制' },
        },
        {
          path: 'settings',
          name: 'settings',
          component: () => import('@/views/settings/SettingsView.vue'),
          meta: { title: '系统设置' },
        },
        {
          path: 'logs',
          name: 'logs',
          component: () => import('@/views/logs/LogsView.vue'),
          meta: { title: '运行日志' },
        },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: { name: 'dashboard' } },
  ],
})

setupGuard(router)

export default router
