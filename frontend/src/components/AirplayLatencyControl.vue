<template>
  <n-flex vertical :size="10" class="latency-control">
    <n-flex align="center" :size="8">
      <n-input-number
        :value="value" :min="0" :max="4000" :step="1" :precision="0" :disabled="busy"
        :input-props="{ 'aria-label': 'AirPlay 2 输出延迟（毫秒）' }"
        style="width: 180px; max-width: 100%"
        @update:value="emit('update:value', $event)"
      >
        <template #suffix>ms</template>
      </n-input-number>
      <n-select v-model:value="step" :options="stepOptions" :disabled="busy"
        aria-label="延迟调节步长" style="width: 128px" />
    </n-flex>
    <n-flex :size="8">
      <n-button :disabled="busy || !valid || value === 0" @click="adjust(-step)">延后 −{{ step }} ms</n-button>
      <n-button :disabled="busy || !valid || value === 4000" @click="adjust(step)">提前 +{{ step }} ms</n-button>
    </n-flex>
    <template v-if="changed">
      <n-text :type="valid ? 'default' : 'error'" aria-live="polite">{{ changeHint }}</n-text>
      <n-flex :size="8">
        <n-button type="primary" :loading="applying" :disabled="busy || !enabled || !valid" @click="emit('apply')">
          应用延迟
        </n-button>
        <n-button :disabled="busy" @click="emit('update:value', savedValue)">还原</n-button>
      </n-flex>
      <n-text depth="3" class="latency-hint">
        {{ enabled ? '应用后会重启播放，请重新连接发送端进行对比。' : '请先保存 AirPlay 2 设置，再单独应用延迟。' }}
      </n-text>
    </template>
  </n-flex>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { NButton, NInputNumber, NSelect, NFlex, NText } from 'naive-ui'
const props = defineProps<{
  value: number | null
  savedValue: number
  enabled: boolean
  busy: boolean
  applying: boolean
}>()
const emit = defineEmits<{ 'update:value': [value: number | null]; apply: [] }>()
const step = ref(10)
const stepOptions = [1, 5, 10, 50, 100].map(value => ({ label: `步长 ${value} ms`, value }))
const valid = computed(() => props.value !== null && Number.isInteger(props.value) && props.value >= 0 && props.value <= 4000)
const changed = computed(() => props.value !== props.savedValue)
const changeHint = computed(() => {
  if (!valid.value) return '请输入 0 到 4000 ms 之间的整数。'
  const delta = props.value! - props.savedValue
  return `${props.savedValue} → ${props.value} ms · ${delta > 0 ? '提前' : '延后'}发送 ${Math.abs(delta)} ms`
})
function adjust(delta: number) {
  if (!valid.value || props.busy) return
  emit('update:value', Math.max(0, Math.min(4000, props.value! + delta)))
}
</script>

<style scoped>
.latency-control { width: 100%; max-width: 600px; }
.latency-hint { font-size: 12px; }
</style>
