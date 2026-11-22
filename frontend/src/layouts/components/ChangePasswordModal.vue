<template>
  <n-modal
    :show="show"
    preset="card"
    title="修改密码"
    style="width: 400px"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <n-form>
      <n-form-item label="原密码">
        <n-input v-model:value="oldPwd" type="password" show-password-on="click" placeholder="请输入原密码" />
      </n-form-item>
      <n-form-item label="新密码">
        <n-input v-model:value="newPwd" type="password" show-password-on="click" placeholder="至少 8 位, 需含字母和数字" />
      </n-form-item>
    </n-form>
    <template #footer>
      <div style="display: flex; justify-content: flex-end; gap: 8px">
        <n-button @click="emit('update:show', false)">取消</n-button>
        <n-button type="primary" :loading="loading" @click="submit">确定</n-button>
      </div>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { NModal, NForm, NFormItem, NInput, NButton, useMessage } from 'naive-ui'
import { changePassword } from '@/api/auth'

defineProps<{ show: boolean }>()
const emit = defineEmits<{ 'update:show': [value: boolean] }>()

const message = useMessage()
const oldPwd = ref('')
const newPwd = ref('')
const loading = ref(false)

async function submit() {
  if (!oldPwd.value || newPwd.value.length < 8 || !/[a-zA-Z]/.test(newPwd.value) || !/\d/.test(newPwd.value)) {
    message.warning('请填写原密码, 新密码至少 8 位且需包含字母和数字')
    return
  }
  loading.value = true
  try {
    await changePassword(oldPwd.value, newPwd.value)
    message.success('密码已修改')
    oldPwd.value = ''
    newPwd.value = ''
    emit('update:show', false)
  } catch (e: any) {
    message.error(e.response?.data?.detail || '修改失败')
  } finally {
    loading.value = false
  }
}
</script>
