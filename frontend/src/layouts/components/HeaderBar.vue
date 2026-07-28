<template>
  <div class="bar">
    <n-button quaternary circle @click="app.toggleSider()">
      <template #icon>
        <n-icon size="20"><MenuOutline /></n-icon>
      </template>
    </n-button>
    <span class="title">{{ pageTitle }}</span>

    <div class="right">
      <n-button quaternary circle @click="app.toggleDark()">
        <template #icon>
          <n-icon size="18"><MoonOutline v-if="!app.dark" /><SunnyOutline v-else /></n-icon>
        </template>
      </n-button>

      <n-dropdown trigger="click" :options="userOptions" @select="onUserSelect">
        <n-button quaternary>
          <template #icon>
            <n-icon size="18"><PersonCircleOutline /></n-icon>
          </template>
          {{ auth.username || 'admin' }}
        </n-button>
      </n-dropdown>
    </div>

    <ChangePasswordModal v-model:show="showPwd" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NIcon, NDropdown, useMessage } from 'naive-ui'
import { MenuOutline, MoonOutline, SunnyOutline, PersonCircleOutline } from '@vicons/ionicons5'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'
import ChangePasswordModal from './ChangePasswordModal.vue'

const app = useAppStore()
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const message = useMessage()

const showPwd = ref(false)

const pageTitle = computed(() => (route.meta.title as string) || '')

const userOptions = [
  { label: '修改密码', key: 'password' },
  { label: '退出登录', key: 'logout' },
]

function onUserSelect(key: string) {
  if (key === 'password') {
    showPwd.value = true
  } else if (key === 'logout') {
    auth.logout()
    message.success('已退出登录')
    router.push({ name: 'login' })
  }
}
</script>

<style scoped>
.bar {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
}
.title {
  font-size: 16px;
  font-weight: 600;
}
.right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 4px;
}
</style>
