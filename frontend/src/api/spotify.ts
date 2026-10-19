import http from './http'

export interface SpotifyTrack {
  title: string
  artist: string
  album: string
  cover: string
  duration_ms: number
  uri: string
  entity_type: string
}

export interface SpotifyBinaryInfo {
  arch: string
  path: string
  installed: boolean
  version: string
  build_date: string
  expires_on: string
  days_left: number | null
  downloading: boolean
  last_error: string
  download_url: string
}

export interface SpotifyStatus {
  enabled: boolean
  running: boolean
  paired: boolean
  active: boolean
  device_name: string
  target_did: string
  target_name: string
  ws_port: number
  stream_url: string
  playback: string
  track: SpotifyTrack | null
  volume: number | null
  binary: SpotifyBinaryInfo
  expires_in_days: number | null
  last_error: string
  api_key_configured: boolean
  arch: string
  pulse_available: boolean
  hostname: string
  idle_reason?: string
}

export async function fetchSpotifyStatus(): Promise<SpotifyStatus> {
  const { data } = await http.get('/spotify/status')
  return data
}

export async function restartSpotify(): Promise<{ ok: boolean; message: string }> {
  const { data } = await http.post('/spotify/restart')
  return data
}

export async function updateSpotifyBinary(): Promise<{ ok: boolean; message: string }> {
  const { data } = await http.post('/spotify/update')
  return data
}

export async function fetchSpotifyPairHint(): Promise<{
  ok: boolean
  device_name: string
  message: string
}> {
  const { data } = await http.post('/spotify/pair')
  return data
}
