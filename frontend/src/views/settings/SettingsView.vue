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
              <n-flex vertical :size="18">
                <n-form-item label="DLNA 端口">
                  <n-input-number v-model:value="form.dlna_port" :min="1" :max="65535" />
                </n-form-item>
                <n-alert v-if="dlnaPortHint" type="warning" :show-icon="true" style="margin: -12px 0 8px">
                  {{ dlnaPortHint }}
                </n-alert>
                <n-form-item label="AirPlay">
                  <div class="airplay-settings">
                    <div class="airplay-toggle">
                      <n-switch
                        v-model:value="form.airplay_mode"
                        checked-value="airplay2" unchecked-value="airplay1"
                        aria-labelledby="airplay2-enable-label"
                      />
                      <span id="airplay2-enable-label">为一个音箱启用 AirPlay 2（实验性）</span>
                    </div>
                    <n-text depth="3" class="airplay-hint">
                      {{ form.airplay_mode === 'airplay2' ? '仅所选音箱使用 AirPlay 2，其他已启用的音箱均使用 AirPlay 1。' : '所有已启用的音箱均使用 AirPlay 1。' }}
                    </n-text>
                    <template v-if="form.airplay_mode === 'airplay2'">
                      <n-select
                        :value="form.airplay2_target_did || null"
                        :options="airplayTargetOptions"
                        :disabled="!airplayTargetOptions.length"
                        :placeholder="airplayTargetOptions.length ? '选择使用 AirPlay 2 的音箱' : '暂无已启用的音箱'"
                        aria-label="AirPlay 2 音箱"
                        style="max-width: 400px"
                        @update:value="form.airplay2_target_did = $event ?? ''"
                      />
                      <n-text v-if="!airplayTargetOptions.length" depth="3" class="airplay-hint">
                        请先在<router-link :to="{ name: 'account' }">账号配置</router-link>中选择音箱。
                      </n-text>
                      <n-alert :type="airplay2Alert.type" :show-icon="true" :bordered="false">
                        {{ airplay2Alert.message }}
                      </n-alert>
                      <details class="airplay-advanced">
                        <summary>高级选项</summary>
                        <div class="airplay-settings">
                          <n-input-number
                            v-model:value="form.airplay2_port" :min="1" :max="65535"
                            :input-props="{ 'aria-label': 'AirPlay 2 端口' }"
                            style="width: 214px; max-width: 100%"
                          >
                            <template #prefix>端口</template>
                          </n-input-number>
                          <n-text depth="3" class="airplay-hint">
                            封面和曲目信息取决于发送端。触屏歌词需匹配到小米曲库中的歌曲。
                          </n-text>
                        </div>
                      </details>
                    </template>
                  </div>
                </n-form-item>
                <n-form-item v-if="form.airplay_mode === 'airplay2'" label="AirPlay 2 延迟">
                  <airplay-latency-control
                    v-model:value="form.airplay2_output_latency_ms"
                    :saved-value="savedAirplay.latency" :enabled="canSaveLatency"
                    :busy="saving || applyingLatency" :applying="applyingLatency"
                    @apply="saveLatency"
                  />
                </n-form-item>
                <n-form-item label="设备离线自动重启">
                  <n-switch v-model:value="form.auto_restart" />
                </n-form-item>
              </n-flex>
            </n-form>
          </n-tab-pane>

          <n-tab-pane name="spotify" tab="Spotify 畅联">
            <n-alert type="info" :show-icon="true" :bordered="false" style="margin-top: 8px">
              通过 Spotify 官方 <b>Soloist</b> 让小爱音箱成为 Spotify Connect 设备。需要
              <b>Spotify Premium</b> 账号生成 Soloist API Key；Soloist 二进制由程序从官方 CDN 下载，
              官方构建 90 天过期后会自动更新。
            </n-alert>
            <n-form :label-placement="labelPlacement" :label-width="labelWidth" :show-feedback="false" style="margin-top: 16px">
              <n-space vertical :size="18">
                <n-form-item label="启用 Spotify 畅联">
                  <n-switch v-model:value="form.spotify_enabled" />
                </n-form-item>
                <n-form-item label="Spotify 设备名">
                  <div class="field-with-tip">
                    <n-input v-model:value="form.spotify_device_name" placeholder="留空则使用目标音箱名称" clearable />
                    <n-text depth="3" class="field-tip">会显示在 Spotify App 的设备列表中。</n-text>
                  </div>
                </n-form-item>
                <n-form-item label="目标音箱">
                  <div class="field-with-tip">
                    <n-select
                      v-if="speakerOptions.length"
                      v-model:value="form.spotify_did"
                      :options="speakerOptions"
                      placeholder="留空使用第一台可用音箱"
                      clearable
                      style="max-width: 400px"
                    />
                    <n-text v-else depth="3" class="field-tip">
                      暂无可用音箱: 请先在「账号配置」完成小米账号登录并选择设备; 留空时自动使用第一台在线音箱。
                    </n-text>
                  </div>
                </n-form-item>
                <n-form-item label="Soloist API Key">
                  <div class="field-with-tip">
                    <n-input
                      v-model:value="form.spotify_api_key"
                      type="password"
                      show-password-on="click"
                      placeholder="未修改请回写脱敏值；清空将删除 Key"
                      clearable
                    />
                    <n-text depth="3" class="field-tip">
                      在
                      <a href="https://developer.spotify.com/dashboard" target="_blank" rel="noopener">
                        Spotify for Developers
                      </a>
                      的「Spotify Soloist API Key」页面生成（需 Premium）。Key 仅保存在本机数据目录，不会上传。
                    </n-text>
                  </div>
                </n-form-item>
              </n-space>
            </n-form>

            <n-space align="center" :size="10" style="margin-top: 20px">
              <n-text strong>运行状态</n-text>
              <n-button size="tiny" :loading="spotifyLoading" @click="loadSpotifyStatus">刷新</n-button>
            </n-space>
            <n-alert v-if="spotifyStatus?.offline" type="error" :show-icon="true" style="margin-top: 8px">
              目标音箱当前离线：已请求暂停 Spotify 并解除活动设备状态，请检查音箱电源/网络。
            </n-alert>
            <n-alert v-if="spotifyStatus?.last_error" type="error" :show-icon="true" style="margin-top: 8px">
              {{ spotifyStatus.last_error }}
            </n-alert>
            <n-alert v-if="spotifyStatus?.idle_reason" type="warning" :show-icon="true" style="margin-top: 8px">
              {{ spotifyStatus.idle_reason }}
            </n-alert>
            <n-descriptions v-if="spotifyStatus" :column="isMobile ? 1 : 2" bordered size="small" style="margin-top: 12px">
              <n-descriptions-item label="运行状态">
                <n-tag :type="spotifyStatus.running ? 'success' : 'default'" size="small">
                  {{ spotifyStatus.running ? '运行中' : '未运行' }}
                </n-tag>
              </n-descriptions-item>
              <n-descriptions-item label="Spotify 配对">
                <n-tag :type="spotifyStatus.paired ? 'success' : 'warning'" size="small">
                  {{ spotifyStatus.paired ? '已配对' : '未配对' }}
                </n-tag>
              </n-descriptions-item>
              <n-descriptions-item label="设备名">{{ spotifyStatus.device_name || '-' }}</n-descriptions-item>
              <n-descriptions-item label="目标音箱">{{ spotifyStatus.target_name || '-' }}</n-descriptions-item>
              <n-descriptions-item label="音箱状态">
                <n-tag :type="spotifyStatus.offline ? 'error' : 'success'" size="small">
                  {{ spotifyStatus.offline ? '离线' : '在线' }}
                </n-tag>
              </n-descriptions-item>
              <n-descriptions-item label="Soloist 版本">
                <span v-if="spotifyStatus.binary.installed">v{{ spotifyStatus.binary.version }}</span>
                <n-tag v-else size="small" type="warning">未安装</n-tag>
                <n-tag v-if="spotifyStatus.binary.downloading" size="small" type="info" style="margin-left: 6px">
                  下载中
                </n-tag>
              </n-descriptions-item>
              <n-descriptions-item label="构建剩余">
                {{ spotifyStatus.binary.days_left != null ? `${spotifyStatus.binary.days_left} 天` : '-' }}
              </n-descriptions-item>
              <n-descriptions-item label="当前曲目" :span="2">
                <n-space align="center" :size="10">
                  <n-image
                    v-if="spotifyStatus?.track?.cover"
                    :src="spotifyStatus.track.cover"
                    width="52"
                    height="52"
                    :preview-disabled="true"
                    style="border-radius: 6px"
                  />
                  <n-text>{{ spotifyTrackText }}</n-text>
                </n-space>
              </n-descriptions-item>
            </n-descriptions>
            <n-space :size="8" style="margin-top: 12px">
              <n-button size="small" :disabled="!spotifyStatus" :loading="spotifyActionLoading" @click="doSpotifyRestart">
                重启 Spotify
              </n-button>
              <n-button size="small" :disabled="!spotifyStatus?.running" :loading="spotifyActionLoading" @click="doSpotifyUpdate">
                更新 Soloist
              </n-button>
              <n-button size="small" :disabled="!spotifyStatus?.running" @click="doSpotifyPair">配对引导</n-button>
            </n-space>
            <n-text depth="3" class="field-tip" style="margin-top: 10px; display: block">
              提示: 保存设置后服务会热重启；首次使用需在手机 Spotify App 的设备列表中选择上方设备名完成配对。
            </n-text>
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
                      DLNA/AirPlay 1 投送时按控制端传来的歌名/歌手搜小米曲库, 命中则触屏音箱显示该曲歌词与封面; 未命中回退上方的小米云默认封面。AirPlay 1 元数据迟到的发送端 (如网易云) 会在开播后自动补发；AirPlay 2 的 stdout 桥接目前不提供元数据。仅对触屏型号生效。
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
      <n-button type="primary" :loading="saving" :disabled="loading || loadFailed || applyingLatency" @click="save">{{ tab === 'service' ? '保存并应用' : '保存并应用 (热重启服务)' }}</n-button>
    </n-space>
  </n-space>
</template>

<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NSpace, NFlex, NCard, NForm, NFormItem, NSwitch, NInputNumber, NInput, NSelect, NButton, NSpin,
  NPopconfirm, NDescriptions, NDescriptionsItem, NAlert, NText, NTabs, NTabPane, NImage,
  NDataTable, NEmpty, NTag, NRadioGroup, NRadioButton, useMessage, type DataTableColumns,
  type SelectOption,
} from 'naive-ui'
import { fetchSettings, fetchStatus, saveSettings, restartProcess, checkUpdate, testNotify, type UpdateInfo } from '@/api/system'
import { fetchLoginLogs, type LoginLogItem } from '@/api/auth'
import {
  fetchSpotifyStatus,
  fetchSpotifyPairHint,
  restartSpotify,
  updateSpotifyBinary,
  type SpotifyStatus,
} from '@/api/spotify'
import { useAppStore } from '@/stores/app'
import { useIsMobile } from '@/composables/useIsMobile'
import AirplayLatencyControl from '@/components/AirplayLatencyControl.vue'

const app = useAppStore()

const message = useMessage()
const loading = ref(false)
// 设置加载失败时禁用保存, 防止把表单默认值全量覆盖到服务端
const loadFailed = ref(false)
const saving = ref(false)
const applyingLatency = ref(false)
const savedAirplay = reactive({ mode: 'airplay1', target: '', port: 7000, latency: 1000 })

// 移动端适配: 窄屏时标签改为上方排列, 避免 160px 左标签挤压输入区
const isMobile = useIsMobile()

const labelPlacement = computed(() => (isMobile.value ? 'top' : 'left'))
const labelWidth = computed(() => (isMobile.value ? undefined : 160))

// tab 与 URL query 同步 (/settings?tab=notify), 刷新不丢位置
const route = useRoute()
const router = useRouter()
const TAB_KEYS = ['playback', 'service', 'spotify', 'touchscreen', 'notify', 'loginlog', 'other', 'about']
const initialTab = String(route.query.tab || '')
const tab = ref(TAB_KEYS.includes(initialTab) ? initialTab : 'playback')

function onTabChange(value: string) {
  router.replace({ query: { ...route.query, tab: value } })
  // 登录日志懒加载: 首次切到该 tab 时拉取
  if (value === 'loginlog' && loginLogs.value.length === 0) loadLoginLogs()
  if (value === 'spotify') {
    loadSpotifyStatus()
    startSpotifyPolling()
  } else {
    stopSpotifyPolling()
  }
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
  airplay_mode: 'airplay1' as 'airplay1' | 'airplay2',
  airplay2_target_did: '',
  airplay2_port: 7000,
  airplay2_output_latency_ms: 1000 as number | null,
  auto_restart: true,
  default_cover_url: '',
  default_audio_id: '',
  touchscreen_lyrics: false,
  notify_type: '',
  notify_feishu_webhook: '',
  notify_feishu_secret: '',
  notify_wxpusher_spt: '',
  spotify_enabled: false,
  spotify_device_name: '',
  spotify_did: '',
  spotify_api_key: '',
})

// 目标音箱候选列表
const speakerOptions = ref<SelectOption[]>([])

// 仅在 DLNA 使用默认端口 8200 时, 提示可能被 fnOS 自带 DLNA 占用
const dlnaPortHint = computed(() => {
  if (form.dlna_port !== 8200) return ''
  return '端口 8200 可能被 fnOS 自带 DLNA 占用导致启动失败。可改为 8201 等空闲端口, 或在 fnOS 设置 → 文件共享协议中关闭自带 DLNA。'
})

const airplayTargetOptions = ref<{ label: string; value: string }[]>([])
const airplayAssignmentSaved = computed(() => savedAirplay.mode === 'airplay2' &&
  form.airplay_mode === savedAirplay.mode &&
  form.airplay2_target_did === savedAirplay.target &&
  form.airplay2_port === savedAirplay.port)
const canSaveLatency = computed(() => !loading.value && !loadFailed.value && airplayAssignmentSaved.value)
const airplay2State = reactive({ available: false, running: false, error: '' })
const airplay2Alert = computed(() => {
  if (!airplay2State.available) {
    return {
      type: 'error' as const,
      message: airplay2State.error ? `AirPlay 2 不可用：${airplay2State.error}` : '当前运行环境缺少 AirPlay 2 组件，请更新 MiAir Next 或检查部署环境。',
    }
  }
  if (!airplayAssignmentSaved.value) {
    return { type: 'info' as const, message: '保存设置以应用此 AirPlay 2 音箱分配，并重启接收服务。' }
  }
  if (airplay2State.error) {
    return { type: 'error' as const, message: `接收器已停止：${airplay2State.error}。保存设置可重试。` }
  }
  if (airplay2State.running) {
    return { type: 'success' as const, message: 'AirPlay 2 接收器运行中，请在发送端的 AirPlay 列表中选择此音箱。' }
  }
  return { type: 'info' as const, message: '保存设置以应用此 AirPlay 2 音箱分配，并重启接收服务。' }
})

// 通知渠道选项
const notifyTypeOptions = [
  { label: '关闭', value: '' },
  { label: '飞书机器人', value: 'feishu' },
  { label: 'WxPusher 极简推送', value: 'wxpusher' },
]

// 预设小米云封面 (audioID: 曲库歌曲 ID, cover: 预览图 CDN)
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
    form.airplay_mode = s.airplay_mode || 'airplay1'
    form.airplay2_target_did = s.airplay2_target_did || ''
    form.airplay2_port = s.airplay2_port || 7000
    form.airplay2_output_latency_ms = s.airplay2_output_latency_ms ?? 1000
    Object.assign(savedAirplay, {
      mode: form.airplay_mode, target: form.airplay2_target_did,
      port: form.airplay2_port, latency: form.airplay2_output_latency_ms,
    })
    airplay2State.available = s.airplay2_available
    airplay2State.running = s.airplay2_running
    airplay2State.error = s.airplay2_error || ''
    airplayTargetOptions.value = Object.values(s.speakers).filter((speaker) => speaker.enabled).map((speaker) => ({
      label: `${speaker.dlna_name || speaker.name} (${speaker.hardware})`,
      value: speaker.did,
    }))
    if (!form.airplay2_target_did && airplayTargetOptions.value.length > 0) {
      form.airplay2_target_did = airplayTargetOptions.value[0].value
    }
    form.auto_restart = s.auto_restart
    form.default_cover_url = s.default_cover_url || ''
    form.default_audio_id = s.default_audio_id || ''
    form.touchscreen_lyrics = s.touchscreen_lyrics ?? false
    form.notify_type = s.notify_type
    form.notify_feishu_webhook = s.notify_feishu_webhook
    form.notify_feishu_secret = s.notify_feishu_secret
    form.notify_wxpusher_spt = s.notify_wxpusher_spt
    form.spotify_enabled = s.spotify_enabled ?? false
    form.spotify_device_name = s.spotify_device_name || ''
    form.spotify_did = s.spotify_did || ''
    form.spotify_api_key = s.spotify_api_key || ''
    speakerOptions.value = Object.values(s.speakers || {})
      .filter((sp) => sp.enabled !== false)
      .map((sp) => ({
        label: sp.dlna_name || sp.name || sp.did,
        value: sp.did,
      }))
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
  if (loading.value || loadFailed.value || saving.value || applyingLatency.value || !validateLatency()) return
  saving.value = true
  try {
    const payload = { ...form, airplay2_output_latency_ms: form.airplay2_output_latency_ms! }
    // 选择首项"内置默认封面"等同于留空, 提交空串避免误存为具体 audioID
    if (payload.default_audio_id === '448161862632079419') {
      payload.default_audio_id = ''
    }
    await saveSettings(payload)
    Object.assign(savedAirplay, {
      mode: payload.airplay_mode, target: payload.airplay2_target_did,
      port: payload.airplay2_port, latency: payload.airplay2_output_latency_ms,
    })
    message.success(tab.value === 'service' ? '已保存，服务正在热重启。' : '已保存, 服务正在热重启')
  } catch (e: any) {
    message.error(e.response?.data?.detail || (tab.value === 'service' ? '保存设置失败。' : '保存失败'))
  } finally {
    saving.value = false
  }
}

function validateLatency(): boolean {
  const value = form.airplay2_output_latency_ms
  if (value === null || !Number.isInteger(value) || value < 0 || value > 4000) {
    message.error('请输入 0 到 4000 ms 之间的整数。')
    return false
  }
  return true
}

async function saveLatency() {
  if (!canSaveLatency.value || saving.value || applyingLatency.value || !validateLatency()) return
  const value = form.airplay2_output_latency_ms!
  if (value === savedAirplay.latency) return
  applyingLatency.value = true
  try {
    // Do not submit other tabs' unsaved drafts while calibrating the speaker.
    await saveSettings({ airplay2_output_latency_ms: value })
    savedAirplay.latency = value
    message.success('延迟已保存，服务正在重启；请重新连接 AirPlay 进行测试。')
  } catch (e: any) {
    message.error(e.response?.data?.detail || '保存延迟失败，已保留未保存的修改。')
  } finally {
    applyingLatency.value = false
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

const spotifyStatus = ref<SpotifyStatus | null>(null)
const spotifyLoading = ref(false)
const spotifyActionLoading = ref(false)
let spotifyTimer: ReturnType<typeof setInterval> | undefined

const spotifyTrackText = computed(() => {
  const t = spotifyStatus.value?.track
  if (!t) return '-'
  return t.artist ? `${t.title} - ${t.artist}` : t.title
})

async function loadSpotifyStatus() {
  spotifyLoading.value = true
  try {
    spotifyStatus.value = await fetchSpotifyStatus()
  } catch {
  } finally {
    spotifyLoading.value = false
  }
}

function startSpotifyPolling() {
  stopSpotifyPolling()
  spotifyTimer = setInterval(loadSpotifyStatus, 5000)
}

function stopSpotifyPolling() {
  if (spotifyTimer) {
    clearInterval(spotifyTimer)
    spotifyTimer = undefined
  }
}

async function doSpotifyRestart() {
  spotifyActionLoading.value = true
  try {
    const r = await restartSpotify()
    message.success(r.message || 'Spotify 服务已重启')
    await loadSpotifyStatus()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '重启 Spotify 失败')
  } finally {
    spotifyActionLoading.value = false
  }
}

async function doSpotifyUpdate() {
  spotifyActionLoading.value = true
  try {
    const r = await updateSpotifyBinary()
    message.info(r.message || '正在更新 Soloist')
    await loadSpotifyStatus()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '更新 Soloist 失败')
  } finally {
    spotifyActionLoading.value = false
  }
}

async function doSpotifyPair() {
  try {
    const r = await fetchSpotifyPairHint()
    message.info(r.message, { duration: 8000 })
  } catch (e: any) {
    message.error(e.response?.data?.detail || '获取配对信息失败')
  }
}

let airplayStatusTimer: ReturnType<typeof setInterval> | undefined
let refreshingAirplayStatus = false

async function refreshAirplayStatus() {
  if (refreshingAirplayStatus || tab.value !== 'service') return
  refreshingAirplayStatus = true
  try {
    const status = await fetchStatus()
    airplay2State.available = status.airplay2_available
    airplay2State.running = status.airplay2_running
    airplay2State.error = status.airplay2_error || ''
  } catch {
    // A service restart may briefly make the status endpoint unavailable.
  } finally {
    refreshingAirplayStatus = false
  }
}

onMounted(() => {
  load()
  airplayStatusTimer = setInterval(refreshAirplayStatus, 5000)
  // 带 ?tab=loginlog 直接进入时也需拉取日志
  if (tab.value === 'loginlog') loadLoginLogs()
  if (tab.value === 'spotify') {
    loadSpotifyStatus()
    startSpotifyPolling()
  }
})

onUnmounted(() => {
  stopSpotifyPolling()
  if (airplayStatusTimer) clearInterval(airplayStatusTimer)
})
</script>

<style scoped>
/* 表单项下方提示文本 */
.field-with-tip {
  width: 100%;
}

.field-tip {
  display: block;
  font-size: 12px;
  margin-top: 4px;
  /* 允许长链接折行 */
  word-break: break-all;
}

.airplay-settings {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
  max-width: 600px;
  min-width: 0;
}
.airplay-hint {
  font-size: 12px;
  overflow-wrap: anywhere;
}
.airplay-toggle {
  display: flex;
  align-items: center;
  gap: 10px;
}
.airplay-toggle .n-switch {
  flex-shrink: 0;
}
.airplay-hint a {
  color: inherit;
  text-underline-offset: 3px;
}
.airplay-advanced summary {
  width: fit-content;
  cursor: pointer;
  font-size: 12px;
}
.airplay-advanced[open] summary {
  margin-bottom: 10px;
}

.qrcode-img {
  margin-top: 8px;
  border-radius: 6px;
}
</style>
