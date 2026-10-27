<template>
  <n-space vertical :size="16">
    <toolbar-card>
      <template #toolbar>
        <n-button size="small" :loading="loading" @click="load">刷新音箱</n-button>
      </template>

      <n-empty v-if="speakers.length === 0" description="暂无运行中的音箱, 请先在账号配置中选择设备" />
      <n-form v-else label-placement="top">
        <n-form-item label="目标音箱">
          <n-select v-model:value="currentDid" :options="speakerOptions" placeholder="选择音箱" />
        </n-form-item>
        <n-form-item label="播放地址 (URL)">
          <n-input-group>
            <n-input v-model:value="url" placeholder="http://... 音频/播放列表地址" />
            <n-button type="primary" :disabled="!currentDid || !url" :loading="playing" @click="doPlay">
              播放
            </n-button>
            <n-button type="warning" :disabled="!currentDid" :loading="stopping" @click="doStop">
              停止
            </n-button>
          </n-input-group>
        </n-form-item>
        <n-form-item label="音量">
          <n-space align="center" :wrap="true" style="width: 100%">
            <n-slider v-model:value="volume" :step="1" :min="0" :max="100" style="flex: 1 1 200px; min-width: 160px" />
            <n-input-number v-model:value="volume" :min="0" :max="100" size="small" style="width: 100px" />
            <n-button size="small" :disabled="!currentDid" @click="doSetVolume">设置</n-button>
            <n-button size="small" :disabled="!currentDid" @click="doGetVolume">读取当前</n-button>
          </n-space>
        </n-form-item>
      </n-form>
    </toolbar-card>
  </n-space>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  NSpace, NButton, NEmpty, NForm, NFormItem, NSelect, NInput, NInputGroup,
  NSlider, NInputNumber, useMessage,
} from 'naive-ui'
import {
  fetchSpeakers, playUrl, stopSpeaker, setVolume, getVolume,
  type SpeakerStatus,
} from '@/api/speakers'
import ToolbarCard from '@/components/ToolbarCard.vue'

const message = useMessage()
const speakers = ref<SpeakerStatus[]>([])
const loading = ref(false)
const playing = ref(false)
const stopping = ref(false)
const currentDid = ref<string | null>(null)
// 预设试音链接: 蜻蜓 FM 直播流 (持续播放, 适合验证播放/停止)
const url = ref('https://lhttp.qtfm.cn/live/4915/64k.mp3')
const volume = ref(50)

const speakerOptions = computed(() =>
  speakers.value.map((s) => ({ label: `${s.dlna_name} (${s.hardware})`, value: s.did })),
)

watch(currentDid, (did) => {
  if (did) doGetVolume()
})

async function doPlay() {
  if (!currentDid.value) return
  playing.value = true
  try {
    const res = await playUrl(currentDid.value, url.value)
    res.ok ? message.success('已下发播放') : message.error('播放失败')
  } catch (e: any) {
    message.error(e.response?.data?.detail || '播放失败')
  } finally {
    playing.value = false
  }
}

async function doStop() {
  if (!currentDid.value) return
  stopping.value = true
  try {
    const res = await stopSpeaker(currentDid.value)
    res.ok ? message.success('已停止') : message.error('停止失败')
  } catch (e: any) {
    message.error(e.response?.data?.detail || '停止失败')
  } finally {
    stopping.value = false
  }
}

async function doSetVolume() {
  if (!currentDid.value) return
  try {
    await setVolume(currentDid.value, volume.value)
    message.success(`音量已设为 ${volume.value}`)
  } catch (e: any) {
    message.error(e.response?.data?.detail || '设置音量失败')
  }
}

async function doGetVolume() {
  if (!currentDid.value) return
  try {
    const res = await getVolume(currentDid.value)
    volume.value = res.volume
  } catch (e: any) {
    message.error(e.response?.data?.detail || '读取音量失败')
  }
}

async function load() {
  loading.value = true
  try {
    speakers.value = await fetchSpeakers()
    if (!currentDid.value && speakers.value.length > 0) {
      currentDid.value = speakers.value[0].did
    }
  } catch (e: any) {
    message.error(e.response?.data?.detail || '加载音箱列表失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
