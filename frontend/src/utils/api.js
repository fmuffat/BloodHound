const BASE = '/api/v1'

async function req(method, path, body = null, params = null) {
  const url = new URL(BASE + path, window.location.origin)
  if (params) Object.entries(params).forEach(([k, v]) => v != null && url.searchParams.set(k, v))

  const res = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: body ? JSON.stringify(body) : null,
  })

  if (!res.ok) {
    const err = new Error(`API error ${res.status}`)
    err.status = res.status
    try { err.detail = (await res.json()).detail } catch { /* non-JSON error body */ }
    throw err
  }
  return res.json()
}

export const api = {
  // Search
  search: (payload)        => req('POST', '/search', payload),
  searchGet: (params)      => req('GET',  '/search', null, params),

  // Filter options
  getOptions: ()           => req('GET', '/options'),

  // Lookups
  lookupMac: (mac, platform, timestamp) => req('GET', `/lookup/mac/${mac}`, null, { platform: platform || null, timestamp: timestamp || null }),
  lookupIp:  (ip)          => req('GET', `/lookup/ip/${ip}`),

  // Status
  status: ()               => req('GET', '/status'),
  testRuckusOne: ()         => req('POST', '/settings/ruckus-one/test'),

  // Workers
  syncRuckus: ()           => req('POST', '/workers/sync-ruckus'),
  resolveDns: ()           => req('POST', '/workers/resolve-dns'),

  // Settings
  getSettings: ()               => req('GET',  '/settings'),
  saveRuckusSettings: (payload) => req('POST', '/settings/ruckus-one', payload),
  getUnleashedSettings: ()      => req('GET',  '/settings/unleashed'),
  saveUnleashedSettings: (p)    => req('POST', '/settings/unleashed', p),
  testUnleashedConnection: (p)  => req('POST', '/settings/unleashed/test', p),
  syncUnleashed: ()             => req('POST', '/workers/sync-unleashed'),

  // Auth
  login: (username, password) => req('POST', '/auth/login', { username, password }),
  logout: () => req('POST', '/auth/logout'),
  getMe: () => req('GET', '/auth/me'),
  changePassword: (p) => req('POST', '/auth/change-password', p),
  eraseClient: (mac, password) => req('POST', '/clients/erase', { mac, password }),

  // Accounts (administrators only)
  listUsers: ()                  => req('GET',    '/users'),
  createUser: (username, role)   => req('POST',   '/users', { username, role }),
  setUserRole: (username, role)  => req('PUT',    `/users/${encodeURIComponent(username)}/role`, { role }),
  resetUserPassword: (username)  => req('POST',   `/users/${encodeURIComponent(username)}/reset-password`),
  deleteUser: (username)         => req('DELETE', `/users/${encodeURIComponent(username)}`),

  // MAC timeline
  getMacTimeline: (mac) => req('GET', `/lookup/mac/${mac}/timeline`),

  // Preferences
  getPreferences: ()      => req('GET',  '/settings/preferences'),
  savePreferences: (p)    => req('POST', '/settings/preferences', p),

  // Retention & disk
  getRetention: ()      => req('GET',  '/settings/retention'),
  saveRetention: (days) => req('POST', '/settings/retention', { days }),
  purgeLogs: ()         => req('POST', '/workers/purge-logs'),
  getDiskUsage: ()      => req('GET',  '/settings/disk'),

  // Active platform (mutually exclusive)
  getActivePlatform: () => req('GET', '/settings/active-platform'),
  setActivePlatform: (platform) => req('POST', '/settings/active-platform', { platform }),

  // SmartZone
  getSmartZoneSettings: () => req('GET', '/settings/smartzone'),
  saveSmartZoneSettings: (p) => req('POST', '/settings/smartzone', p),
  testSmartZone: () => req('POST', '/settings/smartzone/test'),
  getSmartZoneZones: () => req('GET', '/settings/smartzone/zones'),
  getSmartZoneStatus: () => req('GET', '/settings/smartzone/status'),

  // SSL
  uploadSslCert: async (certFile, keyFile) => {
    const form = new FormData()
    form.append('cert', certFile)
    form.append('key', keyFile)
    const res = await fetch(BASE + '/settings/ssl', { method: 'POST', body: form })
    if (!res.ok) { const d = await res.json(); throw new Error(d.detail || 'Upload failed') }
    return res.json()
  },
  generateSelfSigned: (payload) => req('POST', '/settings/ssl/self-signed', payload),

  // Single event ZIP export
  exportEventZip: async (mac, event, password) => {
    // Password in a header, never in the URL (it would land in nginx logs)
    const res = await fetch(BASE + '/investigation/export-zip-event', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Export-Password': encodeURIComponent(password) },
      body: JSON.stringify({ mac, event }),
    })
    if (!res.ok) throw new Error('Export error ' + res.status)
    const blob = await res.blob()
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href     = url
    const cd   = res.headers.get('content-disposition') || ''
    a.download = cd.match(/filename=(.+)/)?.[1] || 'event_export.zip'
    a.click()
    URL.revokeObjectURL(url)
  },

  // ZIP export
  exportZip: async (payload, password) => {
    const res = await fetch(BASE + '/investigation/export-zip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Export-Password': encodeURIComponent(password) },
      body: JSON.stringify(payload),
    })
    if (!res.ok) throw new Error('Export error ' + res.status)
    const blob = await res.blob()
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href     = url
    const cd   = res.headers.get('content-disposition') || ''
    a.download = cd.match(/filename=(.+)/)?.[1] || 'bloodhound_export.zip'
    a.click()
    URL.revokeObjectURL(url)
  },

  // Export
  exportInvestigation: async (payload) => {
    const res = await fetch(BASE + '/investigation/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) throw new Error(`Export error ${res.status}`)
    const blob = await res.blob()
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href     = url
    a.download = `investigation_${Date.now()}.csv`
    a.click()
    URL.revokeObjectURL(url)
  },
}
