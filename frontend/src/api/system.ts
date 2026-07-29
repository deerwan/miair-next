import http from './http'

export interface SpeakerSetting {
  did: string
  name: string
  dlna_name: string
  hardware: string
  enabled: boolean
  compatibility_mode: boolean
}

export interface Settings {
  version: string
  engine_version: string
  hostname: string
  dlna_port: number
  auto_play_on_set_uri: boolean
  mi_did: string
  has_account: boolean
  cookie: string
  dlna_running: boolean
  renderers_count: number
  auto_resume_on_interrupt: boolean
  resume_delay_seconds: number
  default_volume: number
  follow_device_volume: boolean
  auto_restart: boolean
  notify_type: string
  notify_feishu_webhook: string
  notify_feishu_secret: string
  notify_wxpusher_spt: string
  speakers: Record<string, SpeakerSetting>
  need_use_play_music_api: string[]
  device_list?: { miotDID: string; hardware: string; name: string }[]
}

export async function fetchSettings(needDeviceList = false): Promise<Settings> {
  const { data } = await http.get('/settings', {
    params: { need_device_list: needDeviceList },
  })
  return data
}

export async function saveSettings(payload: Partial<Settings> & { password?: string; account?: string }) {
  const { data } = await http.post('/settings', payload)
  return data
}

export interface SystemStatus {
  version: string
  dlna_running: boolean
  renderers_count: number
  hostname: string
  dlna_port: number
  has_account: boolean
  logged_in: boolean
  uptime_seconds: number
  memory_mb: number | null
}

export async function fetchStatus(): Promise<SystemStatus> {
  const { data } = await http.get('/status')
  return data
}

export async function fetchLogs(limit = 200): Promise<{ lines: string[] }> {
  const { data } = await http.get('/logs', { params: { limit } })
  return data
}

export type LogLevel = 'debug' | 'info' | 'warning' | 'error'

export async function fetchLogLevel(): Promise<{ level: LogLevel }> {
  const { data } = await http.get('/logs/level')
  return data
}

export async function setLogLevel(level: LogLevel) {
  const { data } = await http.post('/logs/level', { level })
  return data
}

export interface UpdateInfo {
  current: string
  latest: string | null
  update_available: boolean
  release_url?: string
  release_name?: string
  published_at?: string
  notes?: string
  error?: string
}

export async function checkUpdate(): Promise<UpdateInfo> {
  const { data } = await http.get('/system/check_update')
  return data
}

export async function restartServices() {
  const { data } = await http.post('/system/restart_services')
  return data
}

export async function restartProcess() {
  const { data } = await http.post('/system/restart_process')
  return data
}

export interface NotifyTestResult {
  ok: boolean
  results: { feishu?: boolean; wxpusher?: boolean }
}

export async function testNotify(): Promise<NotifyTestResult> {
  const { data } = await http.post('/system/notify/test')
  return data
}
