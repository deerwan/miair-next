<template>
  <n-card>
    <!-- 空 header 占位: n-card 无 title 时不渲染头部, header-extra 会被一并丢弃 -->
    <template #header><span /></template>
    <template #header-extra>
      <n-button size="small" :loading="loading" @click="load">刷新</n-button>
    </template>

    <n-empty v-if="speakers.length === 0" description="暂无运行中的音箱" />
    <n-data-table
      v-else
      :columns="columns"
      :data="speakers"
      :bordered="false"
      size="small"
    />
  </n-card>
</template>

<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import {
  NCard, NButton, NDataTable, NEmpty, NTag, NSpace, NInput, NSwitch, NTooltip,
  useMessage, type DataTableColumns,
} from 'naive-ui'
import { fetchSpeakers, renameSpeaker, setCompatibilityMode, type SpeakerStatus } from '@/api/speakers'
import { getDeviceModelInfo, getDeviceImageUrl } from '@/utils/deviceModel'

const message = useMessage()
const speakers = ref<SpeakerStatus[]>([])
const loading = ref(false)
const editing = ref<Record<string, string>>({})
const switching = ref<Record<string, boolean>>({})

const columns: DataTableColumns<SpeakerStatus> = [
  { title: '名称', key: 'dlna_name' },
  {
    title: '型号',
    key: 'hardware',
    render: (row) =>
      h(NSpace, { align: 'center', size: 8, wrapItem: false }, () => [
        h('img', {
          src: getDeviceImageUrl(row.hardware),
          width: 40,
          height: 40,
          style: 'object-fit: contain; border-radius: 6px',
          onerror: (e: Event) => ((e.target as HTMLImageElement).style.visibility = 'hidden'),
        }),
        h(NSpace, { vertical: true, size: 0 }, () => [
          h('span', getDeviceModelInfo(row.hardware).model),
          h('span', { style: 'font-size: 12px; color: var(--n-text-color-3, #999)' }, row.hardware),
        ]),
      ]),
  },
  {
    title: '状态',
    key: 'transport_state',
    render: (row) =>
      h(NSpace, { size: 4 }, () => [
        h(NTag, { size: 'small', type: row.transport_state === 'PLAYING' ? 'success' : 'default' }, () => `DLNA: ${row.transport_state}`),
        h(NTag, { size: 'small', type: row.airplay_active ? 'success' : 'default' }, () => `AirPlay: ${row.airplay_active ? '播放' : '空闲'}`),
      ]),
  },
  {
    title: '兼容模式',
    key: 'compatibility_mode',
    render: (row) =>
      h(NTooltip, null, {
        trigger: () =>
          h(NSwitch, {
            size: 'small',
            value: row.compatibility_mode,
            loading: switching.value[row.did],
            'onUpdate:value': (v: boolean) => doToggleCompatibility(row, v),
          }),
        default: () => '开启后使用通用播放接口, 兼容性更好; 关闭则优先无损。修改后自动重启服务',
      }),
  },
  {
    title: '重命名',
    key: 'actions',
    render: (row) =>
      h(NSpace, { size: 4 }, () => [
        h(NInput, {
          size: 'small',
          placeholder: row.dlna_name,
          value: editing.value[row.did] ?? '',
          'onUpdate:value': (v: string) => (editing.value[row.did] = v),
          style: 'width: 140px',
        }),
        h(NButton, { size: 'small', onClick: () => doRename(row) }, () => '保存'),
      ]),
  },
]

async function doToggleCompatibility(row: SpeakerStatus, value: boolean) {
  switching.value[row.did] = true
  try {
    await setCompatibilityMode(row.did, value)
    message.success('已切换兼容模式, 服务正在重启')
    // 乐观更新, 稍后刷新拿到重启后的真实状态
    row.compatibility_mode = value
    setTimeout(load, 2000)
  } catch (e: any) {
    message.error(e.response?.data?.detail || '切换失败')
  } finally {
    switching.value[row.did] = false
  }
}

async function doRename(row: SpeakerStatus) {
  const name = editing.value[row.did]
  if (!name) {
    message.warning('请输入新名称')
    return
  }
  await renameSpeaker(row.did, name)
  message.success('已重命名, 部分投送端需重连生效')
  editing.value[row.did] = ''
  await load()
}

async function load() {
  loading.value = true
  try {
    speakers.value = await fetchSpeakers()
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
