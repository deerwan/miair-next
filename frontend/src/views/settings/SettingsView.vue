<template>
  <n-space vertical :size="16">
    <n-card :content-style="{ paddingTop: '8px' }">
      <n-spin :show="loading">
        <n-tabs v-model:value="tab" type="line" animated @update:value="onTabChange">
          <n-tab-pane name="playback" tab="播放行为">
            <n-form :label-placement="labelPlacement" :label-width="labelWidth" :show-feedback="false" style="margin-top: 8px">
              <n-space vertical :size="18">
                <n-form-item label="设置 URI 后自动播放">
                  <n-switch v-model:value="form.auto_play_on_set_uri" />
                </n-form-item>
                <n-form-item label="被打断后自动恢复">
                  <n-switch v-model:value="form.auto_resume_on_interrupt" />
                </n-form-item>
                <n-form-item label="恢复延迟 (秒)">
                  <n-input-number v-model:value="form.resume_delay_seconds" :min="1" :max="15" />
                </n-form-item>
                <n-form-item label="默认音量">
                  <n-input-number v-model:value="form.default_volume" :min="1" :max="100" />
                </n-form-item>
                <n-form-item label="跟随设备音量">
                  <n-switch v-model:value="form.follow_device_volume" />
                </n-form-item>
              </n-space>
            </n-form>
          </n-tab-pane>

          <n-tab-pane name="service" tab="服务与网络">
            <n-form :label-placement="labelPlacement" :label-width="labelWidth" :show-feedback="false" style="margin-top: 8px">
              <n-space vertical :size="18">
                <n-form-item label="DLNA 端口">
                  <n-input-number v-model:value="form.dlna_port" :min="1" :max="65535" />
                </n-form-item>
                <n-alert v-if="dlnaPortHint" type="warning" :show-icon="true" style="margin: -12px 0 8px">
                  {{ dlnaPortHint }}
                </n-alert>
                <n-form-item label="设备离线自动重启">
                  <n-switch v-model:value="form.auto_restart" />
                </n-form-item>
              </n-space>
            </n-form>
          </n-tab-pane>

          <n-tab-pane name="touchscreen" tab="触屏显示">
            <n-alert type="warning" :show-icon="true" :bordered="false" style="margin-top: 8px">
              实验性功能: 依赖各发送端的元数据格式与小米曲库收录情况, 部分 App/歌曲可能无法显示歌词封面。
            </n-alert>
            <n-form :label-placement="labelPlacement" :label-width="labelWidth" :show-feedback="false" style="margin-top: 16px">
              <n-space vertical :size="18">
                <n-form-item label="小米云默认封面 (可选)">
                  <div class="field-with-tip">
                    <n-select
                      v-model:value="form.default_audio_id"
                      :options="coverOptions"
                      placeholder="留空则使用内置默认封面"
                      clearable
                      filterable
                      style="max-width: 400px"
                    />
                    <n-space v-if="selectedCover" align="center" :size="10" style="margin-top: 8px">
                      <n-image
                        :src="selectedCover"
                        width="76"
                        height="76"
                        :preview-disabled="true"
                        style="border-radius: 6px"
                      />
                      <n-text depth="3" class="field-tip">
                        小米触屏/带屏音箱走小米云播放时, 触屏显示的封面与歌词来源 (audioID: {{ form.default_audio_id }})。选好保存后重新投送即可生效; 留空使用内置默认。
                      </n-text>
                    </n-space>
                    <n-text v-else depth="3" class="field-tip">
                      小米触屏/带屏音箱走小米云播放时, 触屏显示的封面与歌词来源。需为小米曲库中某首歌的 audioID; 留空使用内置默认。
                    </n-text>
                  </div>
                </n-form-item>
                <n-form-item label="触屏歌词匹配">
                  <div class="field-with-tip">
                    <n-space align="center" :size="8">
                      <n-switch v-model:value="form.touchscreen_lyrics" />
                      <n-tag size="small" type="warning" round>Beta</n-tag>
                    </n-space>
                    <n-text depth="3" class="field-tip">
                      DLNA/AirPlay 投送时按控制端传来的歌名/歌手搜小米曲库, 命中则触屏音箱显示该曲歌词与封面; 未命中回退上方的小米云默认封面。AirPlay 元数据迟到的发送端 (如网易云) 会在开播后自动补发。仅对触屏型号生效。
                    </n-text>
                  </div>
                </n-form-item>
              </n-space>
            </n-form>
          </n-tab-pane>

          <n-tab-pane name="notify" tab="通知推送">
            <n-form :label-placement="labelPlacement" :label-width="labelWidth" :show-feedback="false" style="margin-top: 8px">
              <n-space vertical :size="18">
                <n-form-item label="通知方式">
                  <n-select
                    v-model:value="form.notify_type"
                    :options="notifyTypeOptions"
                    style="max-width: 400px"
                  />
                </n-form-item>
                <n-form-item v-if="form.notify_type === 'feishu'" label="飞书机器人 Webhook">
                  <div class="field-with-tip">
                    <n-input
                      v-model:value="form.notify_feishu_webhook"
                      placeholder="完整 Webhook 地址或仅 hook 后的 key"
                      clearable
                    />
                    <n-text depth="3" class="field-tip">
                      飞书群组机器人: https://www.feishu.cn/hc/zh-CN/articles/360024984973
                    </n-text>
                  </div>
                </n-form-item>
                <n-form-item v-if="form.notify_type === 'feishu'" label="加签密钥 (可选)">
                  <div class="field-with-tip">
                    <n-input
                      v-model:value="form.notify_feishu_secret"
                      placeholder="未开启签名校验可留空"
                      clearable
                    />
                    <n-text depth="3" class="field-tip">
                      飞书群组机器人加签密钥, 安全设置中开启签名校验后获得
                    </n-text>
                  </div>
                </n-form-item>
                <n-form-item v-if="form.notify_type === 'wxpusher'" label="WxPusher SPT">
                  <div class="field-with-tip">
                    <n-input
                      v-model:value="form.notify_wxpusher_spt"
                      placeholder="SPT_xxx"
                      clearable
                    />
                    <n-text depth="3" class="field-tip">
                      微信扫描下方二维码, 关注后即可获得专属 SPT
                    </n-text>
                    <n-image
                      :src="WXPUSHER_QRCODE"
                      width="140"
                      alt="WxPusher 极简推送二维码"
                      class="qrcode-img"
                    />
                  </div>
                </n-form-item>
                <n-form-item v-if="form.notify_type" label=" " :show-label="!isMobile">
                  <n-button size="small" :loading="testingNotify" @click="doTestNotify">发送测试消息</n-button>
                </n-form-item>
              </n-space>
            </n-form>
            <n-text depth="3" style="font-size: 12px; display: block; margin-top: 12px">
              小米登录过期/失败、管理面板登录成功/爆破锁定时自动推送提醒, 同一事件 1 小时内只推一次 (面板登录为 1 分钟); 修改后需先保存再测试。
            </n-text>
          </n-tab-pane>

          <n-tab-pane name="loginlog" tab="登录日志">
            <n-space vertical :size="12" style="margin-top: 8px">
              <n-space justify="space-between" align="center">
                <n-text depth="3" style="font-size: 12px">
                  最近 {{ loginLogs.length }} 条面板登录记录 (最多保留 100 条, 新的在前)
                </n-text>
                <n-button size="small" :loading="logsLoading" @click="loadLoginLogs">刷新</n-button>
              </n-space>
              <n-empty v-if="!logsLoading && loginLogs.length === 0" description="暂无登录记录" />
              <n-data-table
                v-else
                :columns="loginLogColumns"
                :data="loginLogs"
                :bordered="false"
                :loading="logsLoading"
                size="small"
                :scroll-x="480"
              />
            </n-space>
          </n-tab-pane>

          <n-tab-pane name="other" tab="其他设置">
            <n-form :label-placement="labelPlacement" :label-width="labelWidth" :show-feedback="false" style="margin-top: 8px">
              <n-space vertical :size="18">
                <n-form-item label="主题">
                  <n-radio-group v-model:value="app.theme">
                    <n-radio-button value="light">亮色</n-radio-button>
                    <n-radio-button value="dark">暗色</n-radio-button>
                    <n-radio-button value="auto">跟随系统</n-radio-button>
                  </n-radio-group>
                </n-form-item>
              </n-space>
            </n-form>
            <n-text depth="3" style="font-size: 12px; display: block; margin-top: 12px">
              主题设置保存在当前浏览器, 修改后立即生效, 无需保存。
            </n-text>
          </n-tab-pane>

          <n-tab-pane name="about" tab="版本与更新">
            <n-space vertical :size="12" style="margin-top: 8px">
              <n-descriptions :column="isMobile ? 1 : 2" label-placement="left" size="small">
                <n-descriptions-item label="应用版本">{{ info.version }}</n-descriptions-item>
                <n-descriptions-item label="协议引擎版本">{{ info.engine_version }}</n-descriptions-item>
                <n-descriptions-item label="主机名">{{ info.hostname }}</n-descriptions-item>
                <n-descriptions-item label="渲染器数量">{{ info.renderers_count }}</n-descriptions-item>
              </n-descriptions>

              <n-space>
                <n-button size="small" :loading="checking" @click="doCheckUpdate">检查更新</n-button>
                <n-popconfirm @positive-click="doRestartProcess">
                  <template #trigger>
                    <n-button size="small">重启整个进程</n-button>
                  </template>
                  确认重启进程? Docker 环境下将由容器策略自动拉起。
                </n-popconfirm>
              </n-space>

              <n-alert
                v-if="update.checked && update.info?.update_available"
                type="success"
                :bordered="false"
                :title="`发现新版本 v${update.info.latest}`"
              >
                <div>当前 v{{ update.info.current }} → 最新 v{{ update.info.latest }}。选择一种方式升级:</div>
                <div v-for="cmd in upgradeCommands" :key="cmd.cmd" style="margin-top: 6px; word-break: break-all">
                  <span style="margin-right: 8px">{{ cmd.label }}:</span>
                  <n-text code>{{ cmd.cmd }}</n-text>
                  <n-button size="tiny" quaternary style="margin-left: 6px" @click="copyCommand(cmd.cmd)">复制</n-button>
                </div>
                <div v-if="update.info.release_url" style="margin-top: 6px">
                  <a :href="update.info.release_url" target="_blank" rel="noopener">查看发行说明</a>
                </div>
              </n-alert>
              <n-alert v-else-if="update.checked && update.info?.error" type="warning" :bordered="false">
                {{ update.info.error }}
              </n-alert>
              <n-alert v-else-if="update.checked" type="default" :bordered="false">
                已是最新版本。
              </n-alert>
            </n-space>
          </n-tab-pane>
        </n-tabs>
      </n-spin>
    </n-card>

    <n-space v-if="tab !== 'about' && tab !== 'loginlog' && tab !== 'other'">
      <n-button type="primary" :loading="saving" :disabled="loadFailed" @click="save">保存并应用 (热重启服务)</n-button>
    </n-space>
  </n-space>
</template>

<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NSpace, NCard, NForm, NFormItem, NSwitch, NInputNumber, NInput, NSelect, NButton, NSpin,
  NPopconfirm, NDescriptions, NDescriptionsItem, NAlert, NText, NTabs, NTabPane, NImage,
  NDataTable, NEmpty, NTag, NRadioGroup, NRadioButton, useMessage, type DataTableColumns,
  type SelectOption,
} from 'naive-ui'
import { fetchSettings, saveSettings, restartProcess, checkUpdate, testNotify, type UpdateInfo } from '@/api/system'
import { fetchLoginLogs, type LoginLogItem } from '@/api/auth'
import { useAppStore } from '@/stores/app'
import { useIsMobile } from '@/composables/useIsMobile'

const app = useAppStore()

const message = useMessage()
const loading = ref(false)
// 设置加载失败时禁用保存, 防止把表单默认值全量覆盖到服务端
const loadFailed = ref(false)
const saving = ref(false)

// 移动端适配: 窄屏时标签改为上方排列, 避免 160px 左标签挤压输入区
const isMobile = useIsMobile()

const labelPlacement = computed(() => (isMobile.value ? 'top' : 'left'))
const labelWidth = computed(() => (isMobile.value ? undefined : 160))

// tab 与 URL query 同步 (/settings?tab=notify), 刷新不丢位置
const route = useRoute()
const router = useRouter()
const TAB_KEYS = ['playback', 'service', 'touchscreen', 'notify', 'loginlog', 'other', 'about']
const initialTab = String(route.query.tab || '')
const tab = ref(TAB_KEYS.includes(initialTab) ? initialTab : 'playback')

function onTabChange(value: string) {
  router.replace({ query: { ...route.query, tab: value } })
  // 登录日志懒加载: 首次切到该 tab 时拉取
  if (value === 'loginlog' && loginLogs.value.length === 0) loadLoginLogs()
}

// 面板登录日志
const loginLogs = ref<LoginLogItem[]>([])
const logsLoading = ref(false)
const loginLogColumns: DataTableColumns<LoginLogItem> = [
  { title: '时间', key: 'time', width: 170 },
  { title: '账号', key: 'username', width: 120, ellipsis: { tooltip: true } },
  { title: 'IP', key: 'ip', width: 140, ellipsis: { tooltip: true } },
  {
    title: '结果',
    key: 'success',
    width: 90,
    render: (row) =>
      h(
        NTag,
        { type: row.success ? 'success' : 'error', size: 'small', round: true },
        { default: () => (row.success ? '成功' : '失败') },
      ),
  },
]

async function loadLoginLogs() {
  logsLoading.value = true
  try {
    loginLogs.value = await fetchLoginLogs()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '加载登录日志失败')
  } finally {
    logsLoading.value = false
  }
}

const form = reactive({
  auto_play_on_set_uri: true,
  auto_resume_on_interrupt: true,
  resume_delay_seconds: 3,
  default_volume: 50,
  follow_device_volume: false,
  dlna_port: 8200,
  auto_restart: true,
  default_cover_url: '',
  default_audio_id: '',
  touchscreen_lyrics: false,
  notify_type: '',
  notify_feishu_webhook: '',
  notify_feishu_secret: '',
  notify_wxpusher_spt: '',
})

// 仅在 DLNA 使用默认端口 8200 时, 提示可能被 fnOS 自带 DLNA 占用
const dlnaPortHint = computed(() => {
  if (form.dlna_port !== 8200) return ''
  return '端口 8200 可能被 fnOS 自带 DLNA 占用导致启动失败。可改为 8201 等空闲端口, 或在 fnOS 设置 → 文件共享协议中关闭自带 DLNA。'
})

// 通知方式候选 (青龙式单选)
const notifyTypeOptions = [
  { label: '关闭', value: '' },
  { label: '飞书机器人', value: 'feishu' },
  { label: 'WxPusher 极简推送', value: 'wxpusher' },
]

// 预设小米云封面 (照搬 songloft 后台下拉, audioID = 曲库真实歌曲 ID, cover = 预览图 CDN)
interface CoverOption extends SelectOption {
  label: string
  value: string
  cover?: string
}
const coverOptions: CoverOption[] = [
  { label: '内置默认封面 (留空时使用)', value: '448161862632079419' },
  { label: '星河雀影', value: '436490277987655', cover: 'https://y.gtimg.cn/music/photo_new/T001R500x500M000000XFSu32aHi5w_7.jpg' },
  { label: '鲸落', value: '2284848025338642973', cover: 'https://y.gtimg.cn/music/photo_new/T002R500x500M000003MUXTN0hp00B_1.jpg' },
  { label: '仲夏涟漪', value: '3032977774822294038', cover: 'https://y.gtimg.cn/music/photo_new/T002R500x500M000000P52RF0ePrMh_1.jpg' },
  { label: '星辰妙漫', value: '3573885250148762567', cover: 'https://y.gtimg.cn/music/photo_new/T002R500x500M000001XSQmy0sbGRe_1.jpg' },
  { label: '所念皆星河', value: '2821554561643067278', cover: 'https://y.gtimg.cn/music/photo_new/T002R500x500M000003mtKhW0DFTMt_3.jpg' },
  { label: '柳岸泊舟', value: '1949968393125757902', cover: 'https://y.gtimg.cn/music/photo_new/T002R500x500M000003zlK2H16UvnY_2.jpg' },
  { label: '花冠少女', value: '1963040443250771008', cover: 'https://y.gtimg.cn/music/photo_new/T002R500x500M000004HWTo41EPK3L_2.jpg' },
  { label: '月夜', value: '703059981413384476', cover: 'https://y.gtimg.cn/music/photo_new/T002R500x500M000002OGzkK12zb2D_1.jpg' },
  { label: '橘子海', value: '2182123779048604035', cover: 'https://y.gtimg.cn/music/photo_new/T002R500x500M000001wIZl83iCjqo_1.jpg' },
  { label: '拾音者', value: '1299407089048748519', cover: 'https://y.gtimg.cn/music/photo_new/T001R500x500M000002knSQ01Ts1vS_0.jpg' },
  { label: '唱片', value: '2234266363446166675', cover: 'https://y.gtimg.cn/music/photo_new/T002R500x500M000003TkJYk3nWUoB_2.jpg' },
  { label: '音符', value: '224322594261696542', cover: 'https://y.gtimg.cn/music/photo_new/T002R500x500M000003k0x5s4Ale28_1.jpg' },
]

// 当前选中项的预览图 (用于下方大图预览)
const selectedCover = computed(() => {
  const opt = coverOptions.find((o) => o.value === form.default_audio_id)
  return opt?.cover || ''
})

// WxPusher 极简推送扫码二维码 (扫码关注后微信内获得 SPT)
const WXPUSHER_QRCODE =
  'https://wxpusher.zjiecode.com/api/qrcode/RwjGLMOPTYp35zSYQr0HxbCPrV9eU0wKVBXU1D5VVtya0cQXEJWPjqBdW3gKLifS.jpg'

const info = reactive({
  version: '',
  engine_version: '',
  hostname: '',
  renderers_count: 0,
})

async function load() {
  loading.value = true
  loadFailed.value = false
  try {
    const s = await fetchSettings()
    form.auto_play_on_set_uri = s.auto_play_on_set_uri
    form.auto_resume_on_interrupt = s.auto_resume_on_interrupt
    form.resume_delay_seconds = s.resume_delay_seconds
    form.default_volume = s.default_volume
    form.follow_device_volume = s.follow_device_volume
    form.dlna_port = s.dlna_port
    form.auto_restart = s.auto_restart
    form.default_cover_url = s.default_cover_url || ''
    form.default_audio_id = s.default_audio_id || ''
    form.touchscreen_lyrics = s.touchscreen_lyrics ?? false
    form.notify_type = s.notify_type
    form.notify_feishu_webhook = s.notify_feishu_webhook
    form.notify_feishu_secret = s.notify_feishu_secret
    form.notify_wxpusher_spt = s.notify_wxpusher_spt
    info.version = s.version
    info.engine_version = s.engine_version
    info.hostname = s.hostname
    info.renderers_count = s.renderers_count
  } catch (e: any) {
    // 加载失败必须拦住保存: 表单停留在硬编码默认值上, 此时提交会把
    // 真实端口/通知配置等全部覆盖成默认值 (丢配置陷阱)
    loadFailed.value = true
    message.error(e.response?.data?.detail || '加载设置失败, 请刷新重试; 已禁用保存以防默认值覆盖现有配置')
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    const payload = { ...form }
    // 选择首项"内置默认封面"等同于留空, 提交空串避免误存为具体 audioID
    if (payload.default_audio_id === '448161862632079419') {
      payload.default_audio_id = ''
    }
    await saveSettings(payload)
    message.success('已保存, 服务正在热重启')
  } catch (e: any) {
    message.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function doRestartProcess() {
  await restartProcess()
  message.info('进程正在重启, 稍后请刷新页面')
}

const testingNotify = ref(false)

async function doTestNotify() {
  testingNotify.value = true
  try {
    const r = await testNotify()
    const parts: string[] = []
    if (r.results.feishu !== undefined) parts.push(`飞书 ${r.results.feishu ? '✓' : '✗'}`)
    if (r.results.wxpusher !== undefined) parts.push(`WxPusher ${r.results.wxpusher ? '✓' : '✗'}`)
    if (r.ok) message.success(`测试消息已发送: ${parts.join(' / ')}`)
    else message.error(`发送失败: ${parts.join(' / ')}`)
  } catch (e: any) {
    message.error(e.response?.data?.detail || '测试失败')
  } finally {
    testingNotify.value = false
  }
}

const checking = ref(false)
const update = reactive<{ checked: boolean; info: UpdateInfo | null }>({
  checked: false,
  info: null,
})

// 升级命令 (一键安装脚本部署 / 手动 docker 部署 两种场景)
const upgradeCommands = [
  { label: '脚本部署', cmd: './manage.sh update' },
  { label: '手动部署', cmd: 'docker pull mrdeer1997/miair-next:latest' },
]

async function copyCommand(cmd: string) {
  try {
    await navigator.clipboard.writeText(cmd)
    message.success('已复制')
  } catch {
    message.error('复制失败, 请手动选中复制')
  }
}

async function doCheckUpdate() {
  checking.value = true
  try {
    update.info = await checkUpdate()
    update.checked = true
  } catch (e: any) {
    message.error(e.response?.data?.detail || '检查更新失败')
  } finally {
    checking.value = false
  }
}

onMounted(() => {
  load()
  // 带 ?tab=loginlog 直接进入时也需拉取日志
  if (tab.value === 'loginlog') loadLoginLogs()
})
</script>

<style scoped>
/* 输入框 + 下方说明文字 (青龙式 extra) */
.field-with-tip {
  width: 100%;
}

.field-tip {
  display: block;
  font-size: 12px;
  margin-top: 4px;
  /* 长链接在窄屏上允许任意位置折行, 避免撑破容器 */
  word-break: break-all;
}

.qrcode-img {
  margin-top: 8px;
  border-radius: 6px;
}
</style>
