<template>
  <div class="auth-wrap">
    <n-card class="auth-card" title="MiAir Next 登录">
      <n-form @keyup.enter="submit">
        <n-form-item label="用户名">
          <n-input v-model:value="username" placeholder="用户名" />
        </n-form-item>
        <n-form-item label="密码">
          <n-input v-model:value="password" type="password" show-password-on="click" placeholder="密码" />
        </n-form-item>
        <n-button type="primary" block :loading="loading" @click="submit">登录</n-button>
      </n-form>
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NForm, NFormItem, NInput, NButton, useMessage } from 'naive-ui'
import { checkLoginStatus, login } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const message = useMessage()
const auth = useAuthStore()

const username = ref('admin')
const password = ref('')
const loading = ref(false)

onMounted(async () => {
  // 未初始化则引导到 Setup
  try {
    const { initialized } = await checkLoginStatus()
    if (!initialized) router.replace({ name: 'setup' })
  } catch {
    /* 忽略, 留在登录页 */
  }
})

async function submit() {
  if (!username.value || !password.value) {
    message.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    const { access_token } = await login(username.value, password.value)
    auth.setToken(access_token)
    auth.username = username.value
    message.success('登录成功')
    router.replace({ name: 'dashboard' })
  } catch (e: any) {
    message.error(e.response?.data?.detail || '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.auth-wrap {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #6a5acd22, #20b2aa22);
}
.auth-card {
  width: 380px;
  max-width: 90vw;
}
</style>
