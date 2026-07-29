<template>
  <n-space vertical :size="16">
    <n-card>
      <!-- 空 header 占位: n-card 无 title 时不渲染头部, header-extra 会被一并丢弃 -->
      <template #header><span /></template>
      <template #header-extra>
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
          </n-input-group>
        </n-form-item>
        <n-form-item label="传输控制">
          <n-space>
            <n-button :disabled="!currentDid" @click="doPause">暂停</n-button>
            <n-button :disabled="!currentDid" @click="doStop">停止</n-button>
          </n-space>
        </n-form-item>
        <n-form-item label="音量">
          <n-space align="center" style="width: 100%">
            <n-slider v-model:value="volume" :step="1" :min="0" :max="100" style="width: 240px" />
            <n-input-number v-model:value="volume" :min="0" :max="100" size="small" style="width: 100px" />
            <n-button size="small" :disabled="!currentDid" @click="doSetVolume">设置</n-button>
            <n-button size="small" :disabled="!currentDid" @click="doGetVolume">读取当前</n-button>
          </n-space>
        </n-form-item>
      </n-form>
    </n-card>
  </n-space>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  NSpace, NCard, NButton, NEmpty, NForm, NFormItem, NSelect, NInput, NInputGroup,
  NSlider, NInputNumber, useMessage,
} from 'naive-ui'
import {
  fetchSpeakers, playUrl, pauseSpeaker, stopSpeaker, setVolume, getVolume,
  type SpeakerStatus,
} from '@/api/speakers'

const message = useMessage()
const speakers = ref<SpeakerStatus[]>([])
const loading = ref(false)
const playing = ref(false)
const currentDid = ref<string | null>(null)
const url = ref('')
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

async function doPause() {
  if (!currentDid.value) return
  await pauseSpeaker(currentDid.value)
  message.success('已暂停')
}

async function doStop() {
  if (!currentDid.value) return
  await stopSpeaker(currentDid.value)
  message.success('已停止')
}

async function doSetVolume() {
  if (!currentDid.value) return
  await setVolume(currentDid.value, volume.value)
  message.success(`音量已设为 ${volume.value}`)
}

async function doGetVolume() {
  if (!currentDid.value) return
  try {
    const res = await getVolume(currentDid.value)
    volume.value = res.volume
  } catch {
    /* 忽略读取失败 */
  }
}

async function load() {
  loading.value = true
  try {
    speakers.value = await fetchSpeakers()
    if (!currentDid.value && speakers.value.length > 0) {
      currentDid.value = speakers.value[0].did
    }
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
