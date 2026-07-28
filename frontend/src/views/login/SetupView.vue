<template>
  <div class="auth-wrap">
    <n-card class="auth-card" title="首次使用 · 创建管理员">
      <n-form @keyup.enter="submit">
        <n-form-item label="管理员用户名">
          <n-input v-model:value="username" placeholder="默认 admin" />
        </n-form-item>
        <n-form-item label="设置密码">
          <n-input v-model:value="password" type="password" show-password-on="click" placeholder="至少 6 位" />
        </n-form-item>
        <n-form-item label="确认密码">
          <n-input v-model:value="confirm" type="password" show-password-on="click" placeholder="再次输入" />
        </n-form-item>
        <n-button type="primary" block :loading="loading" @click="submit">创建并登录</n-button>
      </n-form>
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NForm, NFormItem, NInput, NButton, useMessage } from 'naive-ui'
import { checkLoginStatus, setupAdmin } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const message = useMessage()
const auth = useAuthStore()

const username = ref('admin')
const password = ref('')
const confirm = ref('')
const loading = ref(false)

onMounted(async () => {
  // 已初始化则不允许再次 setup
  try {
    const { initialized } = await checkLoginStatus()
    if (initialized) router.replace({ name: 'login' })
  } catch {
    /* 忽略 */
  }
})

async function submit() {
  if (password.value.length < 6) {
    message.warning('密码至少 6 位')
    return
  }
  if (password.value !== confirm.value) {
    message.warning('两次密码不一致')
    return
  }
  loading.value = true
  try {
    const { access_token } = await setupAdmin(username.value, password.value)
    auth.setToken(access_token)
    auth.username = username.value
    message.success('管理员创建成功')
    router.replace({ name: 'dashboard' })
  } catch (e: any) {
    message.error(e.response?.data?.detail || '创建失败')
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
