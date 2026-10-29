<template>
  <img v-if="url" :src="url" alt="AirPlay 封面" class="artwork" @error="clearUrl" />
</template>

<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { fetchAirplayArtwork } from '@/api/speakers'

const props = defineProps<{ did: string; artworkId: string }>()
const url = ref('')

function clearUrl() {
  if (url.value) URL.revokeObjectURL(url.value)
  url.value = ''
}

watch(() => [props.did, props.artworkId], async ([did, artworkId], _, onCleanup) => {
  const controller = new AbortController()
  onCleanup(() => { controller.abort(); clearUrl() })
  clearUrl()
  if (!artworkId) return
  try {
    const blob = await fetchAirplayArtwork(did, artworkId, controller.signal)
    if (!controller.signal.aborted) url.value = URL.createObjectURL(blob)
  } catch {
    // Optional sender artwork may disappear between a status update and GET.
  }
}, { immediate: true })

onUnmounted(clearUrl)
</script>

<style scoped>
.artwork { width: 64px; height: 64px; object-fit: cover; border-radius: 6px; }
</style>
