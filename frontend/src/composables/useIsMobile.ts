import { onBeforeUnmount, readonly, ref, type Ref } from 'vue'

/** 移动端断点 (px): 窄于此值视为移动端 */
export const MOBILE_BREAKPOINT = 640

/**
 * 响应式判断当前是否为窄屏 (移动端)。
 *
 * 组件内调用, 自动在卸载时移除 resize 监听; 断点默认 640px, 可按需覆盖。
 */
export function useIsMobile(breakpoint = MOBILE_BREAKPOINT): Readonly<Ref<boolean>> {
  const isMobile = ref(window.innerWidth < breakpoint)

  function onResize() {
    isMobile.value = window.innerWidth < breakpoint
  }

  window.addEventListener('resize', onResize)
  onBeforeUnmount(() => window.removeEventListener('resize', onResize))

  return readonly(isMobile)
}
