<template>
  <n-card>
    <!--
      n-card 无 title 时不渲染 header, 会连带丢弃 header-extra。
      这里用空 span 占位保住 header, 把工具栏挂到右侧 header-extra,
      避免每个页面重复此 hack。
    -->
    <template #header><span /></template>
    <template #header-extra>
      <slot name="toolbar" />
    </template>
    <slot />
  </n-card>
</template>

<script setup lang="ts">
import { NCard } from 'naive-ui'
</script>
