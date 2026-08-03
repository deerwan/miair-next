<template>
  <n-layout has-sider position="absolute">
    <n-layout-sider
      bordered
      collapse-mode="width"
      :collapsed-width="64"
      :width="220"
      :collapsed="app.collapsed"
      :content-style="{ display: 'flex', flexDirection: 'column', height: '100%' }"
      @collapse="app.collapsed = true"
      @expand="app.collapsed = false"
    >
      <div class="logo">
        <img class="logo-icon" src="/logo.png" alt="logo" />
        <span v-show="!app.collapsed" class="logo-text">MiAir Next</span>
      </div>
      <div class="menu-wrap">
        <SideMenu :collapsed="app.collapsed" />
      </div>
      <!-- 底部折叠条 (青龙式): 固定在侧栏左下角 -->
      <div class="collapse-bar" :class="{ collapsed: app.collapsed }" @click="app.toggleSider()">
        <n-icon size="18">
          <ChevronForwardOutline v-if="app.collapsed" />
          <ChevronBackOutline v-else />
        </n-icon>
      </div>
    </n-layout-sider>

    <n-layout>
      <n-layout-header bordered class="header">
        <HeaderBar />
      </n-layout-header>
      <!-- 内容区是唯一滚动容器, 顶栏固定不随页面滚动 -->
      <n-layout-content
        position="absolute"
        style="top: 56px; bottom: 0"
        content-style="padding: 16px;"
        :native-scrollbar="false"
      >
        <router-view />
      </n-layout-content>
    </n-layout>
  </n-layout>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { NLayout, NLayoutSider, NLayoutHeader, NLayoutContent, NIcon } from 'naive-ui'
import { ChevronBackOutline, ChevronForwardOutline } from '@vicons/ionicons5'
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
  width: 22px;
  height: 22px;
  object-fit: contain;
}
.menu-wrap {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
}
.collapse-bar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  height: 44px;
  padding: 0 20px;
  border-top: 1px solid var(--n-border-color, rgba(128, 128, 128, 0.18));
  cursor: pointer;
  color: var(--n-text-color-3, #999);
  transition: color 0.2s;
}
.collapse-bar:hover {
  color: var(--n-primary-color, #18a058);
}
.collapse-bar.collapsed {
  justify-content: center;
  padding: 0;
}
.header {
  height: 56px;
  display: flex;
  align-items: center;
  padding: 0 16px;
}
</style>
