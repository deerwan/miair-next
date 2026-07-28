<template>
  <n-layout has-sider position="absolute">
    <n-layout-sider
      bordered
      collapse-mode="width"
      :collapsed-width="64"
      :width="220"
      :collapsed="app.collapsed"
      show-trigger="bar"
      @collapse="app.collapsed = true"
      @expand="app.collapsed = false"
    >
      <div class="logo">
        <span class="logo-icon">🎵</span>
        <span v-show="!app.collapsed" class="logo-text">MiAir Next</span>
      </div>
      <SideMenu :collapsed="app.collapsed" />
    </n-layout-sider>

    <n-layout>
      <n-layout-header bordered class="header">
        <HeaderBar />
      </n-layout-header>
      <n-layout-content content-style="padding: 16px;" :native-scrollbar="false">
        <router-view />
      </n-layout-content>
    </n-layout>
  </n-layout>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { NLayout, NLayoutSider, NLayoutHeader, NLayoutContent } from 'naive-ui'
import { useAppStore } from '@/stores/app'
import SideMenu from './components/SideMenu.vue'
import HeaderBar from './components/HeaderBar.vue'

const app = useAppStore()

// 响应式: 窄屏自动折叠
function onResize() {
  if (window.innerWidth < 768) {
    app.collapsed = true
  }
}

onMounted(() => {
  onResize()
  window.addEventListener('resize', onResize)
})
onUnmounted(() => window.removeEventListener('resize', onResize))
</script>

<style scoped>
.logo {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 56px;
  padding: 0 20px;
  font-weight: 600;
  font-size: 16px;
  white-space: nowrap;
  overflow: hidden;
}
.logo-icon {
  font-size: 22px;
}
.header {
  height: 56px;
  display: flex;
  align-items: center;
  padding: 0 16px;
}
</style>
