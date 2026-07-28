<template>
  <n-menu
    :collapsed="collapsed"
    :collapsed-width="64"
    :collapsed-icon-size="22"
    :options="menuOptions"
    :value="activeKey"
    @update:value="onSelect"
  />
</template>

<script setup lang="ts">
import { computed, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NIcon, NMenu, type MenuOption } from 'naive-ui'
import {
  SpeedometerOutline,
  HardwareChipOutline,
  PersonOutline,
  MusicalNotesOutline,
  SettingsOutline,
  DocumentTextOutline,
} from '@vicons/ionicons5'

defineProps<{ collapsed: boolean }>()

const route = useRoute()
const router = useRouter()
const activeKey = computed(() => route.name as string)

function renderIcon(icon: any) {
  return () => h(NIcon, null, { default: () => h(icon) })
}

const menuOptions: MenuOption[] = [
  { label: '总览', key: 'dashboard', icon: renderIcon(SpeedometerOutline) },
  { label: '设备管理', key: 'devices', icon: renderIcon(HardwareChipOutline) },
  { label: '账号配置', key: 'account', icon: renderIcon(PersonOutline) },
  { label: '播放控制', key: 'playback', icon: renderIcon(MusicalNotesOutline) },
  { label: '系统设置', key: 'settings', icon: renderIcon(SettingsOutline) },
  { label: '运行日志', key: 'logs', icon: renderIcon(DocumentTextOutline) },
]

function onSelect(key: string) {
  router.push({ name: key })
}
</script>
