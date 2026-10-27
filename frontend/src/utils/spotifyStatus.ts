import type { SpotifySpeakerStatus } from '@/api/speakers'

export interface SpotifyTagInfo {
  type: 'default' | 'success' | 'warning' | 'error' | 'info'
  text: string
}

/** 将后端汇总的 Spotify 状态映射为展示标签 */
export function spotifyTag(s?: SpotifySpeakerStatus | null): SpotifyTagInfo {
  if (!s) return { type: 'default', text: 'Spotify: -' }
  if (!s.enabled) return { type: 'default', text: 'Spotify: 未启用' }
  if (!s.configured) return { type: 'warning', text: 'Spotify: 未配置 Key' }
  if (!s.is_target) return { type: 'default', text: 'Spotify: 未绑定' }
  if (s.offline) return { type: 'error', text: 'Spotify: 设备离线' }
  if (!s.running) {
    return { type: s.last_error ? 'error' : 'warning', text: 'Spotify: 未运行' }
  }
  if (!s.paired) return { type: 'warning', text: 'Spotify: 待配对' }
  // 非当前活动播放设备时展示为空闲
  if (!s.active) return { type: 'default', text: 'Spotify: 空闲' }
  if (s.playback === 'playing' || s.playback === 'buffering') {
    return { type: 'success', text: 'Spotify: 播放中' }
  }
  if (s.playback === 'paused') return { type: 'info', text: 'Spotify: 已暂停' }
  return { type: 'default', text: 'Spotify: 空闲' }
}

/** 提取当前播放曲目文本摘要 */
export function spotifyTrackText(s?: SpotifySpeakerStatus | null): string {
  if (!s || !s.is_target || !s.running || !s.paired || !s.active || s.offline) return ''
  const t = s.track
  if (!t || !t.title) return ''
  return t.artist ? `${t.title} — ${t.artist}` : t.title
}
