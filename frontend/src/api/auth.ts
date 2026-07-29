import http from './http'

export interface TokenResponse {
  access_token: string
  token_type: string
}

export async function checkLoginStatus(): Promise<{ initialized: boolean }> {
  const { data } = await http.get('/login/status')
  return data
}

export async function setupAdmin(username: string, password: string): Promise<TokenResponse> {
  const { data } = await http.post('/login/setup', { username, password })
  return data
}

export async function login(username: string, password: string): Promise<TokenResponse> {
  const { data } = await http.post('/login', { username, password })
  return data
}

export async function changePassword(oldPassword: string, newPassword: string) {
  const { data } = await http.post('/login/password', {
    old_password: oldPassword,
    new_password: newPassword,
  })
  return data
}

export async function fetchMe(): Promise<{ username: string }> {
  const { data } = await http.get('/me')
  return data
}

export interface LoginLogItem {
  username: string
  ip: string
  success: boolean
  time: string
}

export async function fetchLoginLogs(limit = 50): Promise<LoginLogItem[]> {
  const { data } = await http.get('/login/logs', { params: { limit } })
  return data.logs
}
