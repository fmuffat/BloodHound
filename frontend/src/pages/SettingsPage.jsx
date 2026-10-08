import { useState, useEffect } from 'react'
import { api } from '../utils/api'
import { usePrefs } from '../utils/PrefsContext'
import { useUser, isAdmin, canManageLogs } from '../utils/UserContext'
import UsersPanel from './UsersPanel'
import './SettingsPage.css'

const REGIONS = [
  { value: 'EU',   label: 'Europe',       url: 'api.eu.ruckus.cloud' },
  { value: 'US',   label: 'North America', url: 'api.ruckus.cloud' },
  { value: 'ASIA', label: 'Asia',          url: 'api.asia.ruckus.cloud' },
]

// ── Unleashed Panel ───────────────────────────────────────────────────────────

function UnleashedPanel({ onEnabledChange }) {
  const [ul, setUl]           = useState({ ip: '', username: 'admin', password: '', enabled: false })
  const [saving, setSaving]   = useState(false)
  const [saved, setSaved]     = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [error, setError]     = useState(null)

  useEffect(() => {
    api.getUnleashedSettings().then(data => {
      if (data) setUl(prev => ({ ...prev, ...data }))
    }).catch(() => {})
  }, [])

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      await api.saveUnleashedSettings(ul)
      setSaved(true)
      if (onEnabledChange) onEnabledChange(ul.enabled)
      setTimeout(() => setSaved(false), 3000)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const result = await api.testUnleashedConnection(ul)
      setTestResult(result)
    } catch (e) {
      setTestResult({ ok: false, error: e.message })
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="settings-panel">
      <div className="panel-title">Unleashed</div>
      <div className="panel-desc">
        Connect to a Ruckus Unleashed Master AP to enrich flow logs with
        client identity, OS type and SSID information.
      </div>

      <div className="form-section">
        <div className="form-label">Enable Unleashed integration</div>
        <label className="toggle-label">
          <input
            type="checkbox"
            checked={ul.enabled}
            onChange={e => setUl(s => ({ ...s, enabled: e.target.checked }))}
          />
          <span className="toggle-text">{ul.enabled ? 'Enabled' : 'Disabled'}</span>
        </label>
      </div>

      <div className="form-section">
        <div className="form-label">Master AP IP address</div>
        <input
          className="form-input"
          value={ul.ip}
          onChange={e => setUl(s => ({ ...s, ip: e.target.value }))}
          placeholder="e.g. 192.168.1.100"
          spellCheck={false}
        />
        <div className="form-hint">IP address of the Unleashed Master AP</div>
      </div>

      <div className="form-row">
        <div className="form-section">
          <div className="form-label">Username</div>
          <input
            className="form-input"
            value={ul.username}
            onChange={e => setUl(s => ({ ...s, username: e.target.value }))}
            placeholder="admin"
          />
        </div>
        <div className="form-section">
          <div className="form-label">Password</div>
          <input
            className="form-input"
            type="password"
            value={ul.password}
            onChange={e => setUl(s => ({ ...s, password: e.target.value }))}
            placeholder="••••••••••••••••"
          />
        </div>
      </div>

      {ul.ip && (
        <div className="form-section">
          <div className="form-label">API Endpoint</div>
          <div className="endpoint-display">
            https://{ul.ip}/admin/api/unv1/cmd.jsp
          </div>
        </div>
      )}

      {error && <div className="form-error">⚠ {error}</div>}
      {saved && <div className="form-success">✓ Settings saved</div>}

      <div className="form-actions">
        <button className="btn-ghost" onClick={handleTest} disabled={testing}>
          {testing ? 'Testing...' : 'Test connection'}
        </button>
        <button className="btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving...' : 'Save & sync'}
        </button>
      </div>

      {testResult && (
        <div className={`test-result ${testResult.ok ? 'test-ok' : 'test-fail'}`}>
          {testResult.ok
            ? `✓ Connected — ${testResult.aps} AP(s), ${testResult.clients} client(s)`
            : `✗ ${testResult.error}`
          }
        </div>
      )}
    </div>
  )
}

// ── System Panel ──────────────────────────────────────────────────────────────

const TIMEZONES = [
  'Europe/Paris', 'Europe/London', 'Europe/Berlin', 'Europe/Zurich',
  'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
  'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Dubai', 'Australia/Sydney',
  'UTC',
]

function PreferencesPanel() {
  const prefs = usePrefs()
  const { timezone = 'Europe/Paris', clock = '24h', save: savePrefs } = prefs || {}
  const [localTz, setLocalTz]       = useState(timezone)
  const [localClock, setLocalClock] = useState(clock)
  const [saving, setSaving]         = useState(false)
  const [saved, setSaved]           = useState(false)

  useEffect(() => { setLocalTz(timezone); setLocalClock(clock) }, [timezone, clock])

  const handleSave = async () => {
    setSaving(true)
    await savePrefs({ timezone: localTz, clock: localClock })
    setSaved(true)
    setTimeout(() => setSaved(false), 3000)
    setSaving(false)
  }

  return (
    <div className="settings-panel">
      <div className="panel-title">Preferences</div>
      <div className="panel-desc">Configure timezone and clock format for the interface.</div>

      <div className="form-section">
        <div className="form-label">Timezone</div>
        <select className="form-input" value={localTz} onChange={e => setLocalTz(e.target.value)}>
          {TIMEZONES.map(tz => <option key={tz} value={tz}>{tz}</option>)}
        </select>
      </div>

      <div className="form-section">
        <div className="form-label">Clock format</div>
        <div className="clock-selector">
          <button className={`clock-btn ${localClock === '24h' ? 'active' : ''}`} onClick={() => setLocalClock('24h')}>24h — 14:30</button>
          <button className={`clock-btn ${localClock === '12h' ? 'active' : ''}`} onClick={() => setLocalClock('12h')}>12h — 2:30 PM</button>
        </div>
      </div>

      {saved && <div className="form-success">✓ Preferences saved</div>}
      <div className="form-actions">
        <button className="btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving...' : 'Save preferences'}
        </button>
      </div>
    </div>
  )
}

function DiskPanel() {
  const [days, setDays]           = useState(30)
  const [saving, setSaving]       = useState(false)
  const [saved, setSaved]         = useState(false)
  const [purging, setPurging]     = useState(false)
  const [disk, setDisk]           = useState(null)
  const [retention, setRetention] = useState(null)
  const [error, setError]         = useState(null)

  useEffect(() => {
    api.getRetention().then(d => { setRetention(d); setDays(d.days) }).catch(() => {})
    api.getDiskUsage().then(setDisk).catch(() => {})
  }, [])

  const handleSave = async () => {
    const d = Math.max(1, Math.min(180, parseInt(days) || 30))
    setDays(d); setSaving(true); setError(null)
    try {
      await api.saveRetention(d)
      setSaved(true)
      setRetention({ days: d, last_purge: new Date().toISOString() })
      setTimeout(() => setSaved(false), 3000)
      api.getDiskUsage().then(setDisk).catch(() => {})
    } catch (e) { setError(e.message) }
    finally { setSaving(false) }
  }

  const handlePurge = async () => {
    setPurging(true); setError(null)
    try {
      await api.purgeLogs()
      setRetention(prev => ({ ...prev, last_purge: new Date().toISOString() }))
      api.getDiskUsage().then(setDisk).catch(() => {})
    } catch (e) { setError(e.message) }
    finally { setPurging(false) }
  }

  const diskPct   = disk?.disk_used_pct || 0
  const diskColor = diskPct > 80 ? 'var(--red)' : diskPct > 60 ? 'var(--yellow)' : 'var(--green)'

  return (
    <div className="settings-panel">
      <div className="panel-title">Disk & Logs</div>
      <div className="panel-desc">Monitor disk usage and manage log retention.</div>

      {disk && (
        <div className="form-section">
          <div className="form-label">Disk Usage</div>
          <div className="disk-info">
            <div className="disk-bar-wrapper">
              <div className="disk-bar" style={{ width: `${diskPct}%`, background: diskColor }} />
            </div>
            <div className="disk-stats">
              <span style={{ color: diskColor }}>{diskPct}% used</span>
              <span className="disk-detail">{disk.disk_used_gb} GB / {disk.disk_total_gb} GB &nbsp;•&nbsp; {disk.disk_free_gb} GB free</span>
            </div>
            <div className="disk-logs">{disk.log_count?.toLocaleString()} log entries indexed</div>
          </div>
        </div>
      )}

      <div className="form-section">
        <div className="form-label">Log Retention</div>
        <div className="retention-input">
          <input type="number" className="form-input retention-days" value={days} min={1} max={180} onChange={e => setDays(e.target.value)} />
          <span className="retention-unit">days</span>
        </div>
        <div className="form-hint">Logs older than {days} days will be automatically deleted. Min: 1, Max: 180.</div>
        {retention?.last_purge && <div className="form-hint">Last purge: {new Date(retention.last_purge).toLocaleString('fr-FR')}</div>}
      </div>

      {error && <div className="form-error">⚠ {error}</div>}
      {saved && <div className="form-success">✓ Retention saved — old logs purged</div>}
      <div className="form-actions">
        <button className="btn-ghost" onClick={handlePurge} disabled={purging}>{purging ? 'Purging...' : 'Purge now'}</button>
        <button className="btn-primary" onClick={handleSave} disabled={saving}>{saving ? 'Saving...' : 'Save & purge'}</button>
      </div>
    </div>
  )
}

// ── SSL Upload Panel ─────────────────────────────────────────────────────────

function SslPanel() {
  const [cn, setCn]               = useState('bloodhound.local')
  const [ip, setIp]               = useState('')
  const [generating, setGenerating] = useState(false)
  const [genResult, setGenResult] = useState(null)
  const [genError, setGenError]   = useState(null)

  const handleGenerate = async () => {
    setGenerating(true)
    setGenResult(null)
    setGenError(null)
    try {
      const r = await api.generateSelfSigned({ cn, ip, days: 3650 })
      setGenResult(r.message)
    } catch (e) {
      setGenError(e.message)
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div className="settings-panel">
      <div className="panel-title">SSL Certificate</div>
      <div className="panel-desc">
        Bloodhound uses a self-signed certificate. Upload your own or regenerate the self-signed one.
      </div>

      {/* Regenerate self-signed */}
      <div className="form-section">
        <div className="form-label">Regenerate self-signed certificate</div>
        <div className="form-row" style={{display:'flex', gap:12}}>
          <div style={{flex:2}}>
            <div className="form-hint">Common Name / DNS</div>
            <input className="form-input" value={cn} onChange={e => setCn(e.target.value)} placeholder="bloodhound.local" />
          </div>
          <div style={{flex:1}}>
            <div className="form-hint">IP Address</div>
            <input className="form-input" value={ip} onChange={e => setIp(e.target.value)} placeholder="e.g. 192.168.1.100" />
          </div>
        </div>
        {genError  && <div className="form-error" style={{marginTop:8}}>⚠ {genError}</div>}
        {genResult && <div className="form-success" style={{marginTop:8}}>✓ {genResult}</div>}
        <div className="form-actions">
          <button className="btn-primary" onClick={handleGenerate} disabled={generating}>
            {generating ? 'Generating...' : '↺ Regenerate self-signed'}
          </button>
        </div>
      </div>

      <div className="drp-divider" />

      {/* Upload custom cert */}
      <div className="form-section">
        <div className="form-label">Upload custom certificate</div>
        <div className="panel-desc">Both files must be in PEM format.</div>
        <SslUploadPanel />
      </div>
    </div>
  )
}

function SslUploadPanel() {
  const [cert, setCert]       = useState(null)
  const [key, setKey]         = useState(null)
  const [uploading, setUploading] = useState(false)
  const [result, setResult]   = useState(null)
  const [error, setError]     = useState(null)

  const handleUpload = async () => {
    if (!cert || !key) { setError('Please select both certificate and key files'); return }
    setUploading(true)
    setError(null)
    setResult(null)
    try {
      const form = new FormData()
      form.append('cert', cert)
      form.append('key', key)
      const res = await fetch('/api/v1/settings/ssl', { method: 'POST', body: form })
      if (!res.ok) {
        const d = await res.json()
        throw new Error(d.detail || 'Upload failed')
      }
      setResult('Certificate uploaded and nginx reloaded successfully')
      setCert(null)
      setKey(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setUploading(false)
    }
  }

  return (
    <div style={{display:'flex', flexDirection:'column', gap:10}}>
      <div className="form-row">
        <div className="form-section">
          <div className="form-label">Certificate (.crt / .pem)</div>
          <input type="file" accept=".crt,.pem,.cer" onChange={e=>setCert(e.target.files[0])}
            style={{fontFamily:'var(--mono)', fontSize:12, color:'var(--text)'}} />
        </div>
        <div className="form-section">
          <div className="form-label">Private key (.key / .pem)</div>
          <input type="file" accept=".key,.pem" onChange={e=>setKey(e.target.files[0])}
            style={{fontFamily:'var(--mono)', fontSize:12, color:'var(--text)'}} />
        </div>
      </div>
      {error  && <div className="form-error">⚠ {error}</div>}
      {result && <div className="form-success">✓ {result}</div>}
      <div className="form-actions">
        <button className="btn-primary" onClick={handleUpload} disabled={uploading || !cert || !key}>
          {uploading ? 'Uploading...' : 'Upload certificate'}
        </button>
      </div>
    </div>
  )
}

// ── Security Panel ───────────────────────────────────────────────────────────

function SecurityPanel() {
  const me = useUser()
  const [currentPwd, setCurrentPwd]   = useState('')
  const [newPwd, setNewPwd]           = useState('')
  const [confirmPwd, setConfirmPwd]   = useState('')
  const [saving, setSaving]           = useState(false)
  const [saved, setSaved]             = useState(false)
  const [error, setError]             = useState(null)

  const handleSave = async () => {
    if (!currentPwd) { setError('Please enter your current password'); return }
    if (!newPwd)     { setError('Please enter a new password'); return }
    if (newPwd !== confirmPwd) { setError('New passwords do not match'); return }
    if (newPwd.length < 8)    { setError('Password must be at least 8 characters'); return }

    setSaving(true)
    setError(null)
    try {
      await api.changePassword({
        current_password: currentPwd,
        new_password: newPwd,
      })
      setSaved(true)
      setCurrentPwd('')
      setNewPwd('')
      setConfirmPwd('')
      setTimeout(() => setSaved(false), 3000)
    } catch (e) {
      setError(e.message === 'API error 401' ? 'Current password is incorrect' : (e.detail || e.message))
    } finally {
      setSaving(false)
    }
  }

  const pwdMatch = newPwd && confirmPwd && newPwd === confirmPwd
  const pwdMismatch = newPwd && confirmPwd && newPwd !== confirmPwd

  return (
    <div className="settings-panel">
      <div className="panel-title">Password</div>
      <div className="panel-desc">
        Change the password of your account, <strong>{me?.username}</strong>.
        Your other open sessions are signed out.
      </div>

      <div className="form-section">
        <div className="form-label">Current password</div>
        <input
          className="form-input"
          type="password"
          value={currentPwd}
          onChange={e => setCurrentPwd(e.target.value)}
          placeholder="••••••••"
          autoComplete="current-password"
        />
      </div>

      <div className="form-section">
        <div className="form-label">New password</div>
        <input
          className="form-input"
          type="password"
          value={newPwd}
          onChange={e => setNewPwd(e.target.value)}
          placeholder="Min. 8 characters"
          autoComplete="new-password"
        />
      </div>

      <div className="form-section">
        <div className="form-label">Confirm new password</div>
        <input
          className={`form-input ${pwdMismatch ? 'input-error' : pwdMatch ? 'input-ok' : ''}`}
          type="password"
          value={confirmPwd}
          onChange={e => setConfirmPwd(e.target.value)}
          placeholder="Repeat new password"
          autoComplete="new-password"
        />
        {pwdMatch    && <div className="form-hint" style={{color:'var(--green,#22c55e)'}}>✓ Passwords match</div>}
        {pwdMismatch && <div className="form-hint" style={{color:'var(--red)'}}>✗ Passwords do not match</div>}
      </div>

      {error && <div className="form-error">⚠ {error}</div>}
      {saved && <div className="form-success">✓ Password changed</div>}

      <div className="form-actions">
        <button className="btn-primary" onClick={handleSave} disabled={saving || pwdMismatch}>
          {saving ? 'Saving...' : 'Change password'}
        </button>
      </div>
    </div>
  )
}

// ── SmartZone Panel ──────────────────────────────────────────────────────────

function SmartZonePanel({ onEnabledChange }) {
  const [sz, setSz]           = useState({ host: '', port: 8443, username: '', password: '', enabled: false, zone_ids: [] })
  const [saving, setSaving]   = useState(false)
  const [saved, setSaved]     = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [error, setError]     = useState(null)
  const [status, setStatus]   = useState(null)
  const [zones, setZones]     = useState([])

  useEffect(() => {
    api.getSmartZoneSettings().then(d => setSz(s => ({...s, ...d}))).catch(() => {})
    api.getSmartZoneStatus().then(setStatus).catch(() => {})
    api.getSmartZoneZones().then(setZones).catch(() => {})
  }, [])

  const toggleZone = (zoneId) => {
    setSz(s => {
      const ids = s.zone_ids || []
      return { ...s, zone_ids: ids.includes(zoneId) ? ids.filter(z => z !== zoneId) : [...ids, zoneId] }
    })
  }

  const handleSave = async () => {
    setSaving(true); setError(null); setSaved(false)
    try {
      await api.saveSmartZoneSettings(sz)
      setSaved(true)
      if (onEnabledChange) onEnabledChange(sz.enabled)
      setTimeout(() => setSaved(false), 3000)
    } catch (e) { setError(e.message) }
    finally { setSaving(false) }
  }

  const handleTest = async () => {
    setTesting(true); setTestResult(null); setError(null)
    try {
      // Save connection fields so the test can use them, but never let a
      // Test click change which platform is active — always re-persist the
      // currently-saved enabled state rather than this form's local value,
      // which may be stale (e.g. right after switching the active platform
      // elsewhere, before this panel has refetched).
      const current = await api.getSmartZoneSettings().catch(() => null)
      await api.saveSmartZoneSettings({ ...sz, enabled: current ? !!current.enabled : false })
      const r = await api.testSmartZone()
      setTestResult(r)
      // Reflect the real persisted enabled state, not whether the test
      // happened to succeed — those are unrelated (a successful test
      // doesn't mean this platform is active, and vice versa).
      if (onEnabledChange) onEnabledChange(current ? !!current.enabled : false)
      setStatus({ status: r.status, message: JSON.stringify(r.detail) })
      // Refresh zones after test
      api.getSmartZoneZones().then(setZones).catch(() => {})
    } catch (e) { setError(e.message) }
    finally { setTesting(false) }
  }

  return (
    <div className="settings-panel">
      <div className="panel-title">SmartZone</div>
      <div className="panel-desc">Connect Bloodhound to a Ruckus SmartZone controller for client enrichment.</div>

      <div className="form-section">
        <div className="form-label">Enable SmartZone integration</div>
        <label className="toggle-label">
          <input
            type="checkbox"
            checked={sz.enabled}
            onChange={e => setSz(s => ({ ...s, enabled: e.target.checked }))}
          />
          <span className="toggle-text">{sz.enabled ? 'Enabled' : 'Disabled'}</span>
        </label>
      </div>

      <div className="form-section">
        <div className="form-label">SmartZone Host</div>
        <input className="form-input" value={sz.host} onChange={e => setSz(s => ({...s, host: e.target.value}))} placeholder="sz.yourdomain.com" />
      </div>

      <div className="form-section">
        <div className="form-label">Port</div>
        <input className="form-input" type="number" value={sz.port} onChange={e => setSz(s => ({...s, port: parseInt(e.target.value)}))} placeholder="8443" />
      </div>

      <div className="form-section">
        <div className="form-label">Username</div>
        <input className="form-input" value={sz.username} onChange={e => setSz(s => ({...s, username: e.target.value}))} autoComplete="username" />
      </div>

      <div className="form-section">
        <div className="form-label">Password</div>
        <input className="form-input" type="password" value={sz.password} onChange={e => setSz(s => ({...s, password: e.target.value}))} autoComplete="current-password" />
      </div>

      {zones.length === 0 && sz.host && <div className="form-hint">Click 'Test connection' to load available zones.</div>}
      {zones.length > 0 && (
        <div className="form-section">
          <div className="form-label">Zones to monitor</div>
          <div className="panel-desc">Select zones where your APs are located. Leave all unchecked to monitor all zones.</div>
          <div style={{display:'flex', flexDirection:'column', gap:6, marginTop:8}}>
            {zones.map(z => (
              <label key={z.id} style={{display:'flex', alignItems:'center', gap:8, cursor:'pointer', fontFamily:'var(--mono)', fontSize:12, color:'var(--text)'}}>
                <input
                  type="checkbox"
                  checked={(sz.zone_ids||[]).includes(z.id)}
                  onChange={() => toggleZone(z.id)}
                  style={{accentColor:'var(--accent)'}}
                />
                <span style={{color:(sz.zone_ids||[]).includes(z.id)?'var(--accent)':'var(--text-dim)'}}>{z.name}</span>
              </label>
            ))}
          </div>
        </div>
      )}

      {status && (
        <div className={status.status === 'ok' ? 'form-success' : 'form-error'}>
          {status.status === 'ok' ? '✓' : '⚠'} {status.message}
        </div>
      )}
      {error   && <div className="form-error">⚠ {error}</div>}
      {saved   && <div className="form-success">✓ Settings saved</div>}
      {testResult && testResult.status === 'ok' && (
        <div className="form-success">
          ✓ Connected — APs: {testResult.detail?.aps}, WLANs: {testResult.detail?.wlans}, Clients: {testResult.detail?.clients}
        </div>
      )}

      <div className="form-actions">
        <button className="btn-ghost" onClick={handleTest} disabled={testing || !sz.host}>
          {testing ? 'Testing...' : 'Test connection'}
        </button>
        <button className="btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>
    </div>
  )
}

// ── Main Settings Page ────────────────────────────────────────────────────────

export default function SettingsPage() {
  const user = useUser()
  if (isAdmin(user)) return <AdminSettingsPage />
  return <LimitedSettingsPage logs={canManageLogs(user)} />
}

// Logs managers: Disk & Logs + Password. Read-only accounts: Password only.
function LimitedSettingsPage({ logs }) {
  const [tab, setTab] = useState(logs ? 'disk' : 'security')
  return (
    <div className="settings-page">
      <div className="settings-layout">
        <div className="settings-tabs">
          {logs && (
            <button className={`tab-item ${tab === 'disk' ? 'active' : ''}`} onClick={() => setTab('disk')}>
              <span className="tab-icon">💾</span>
              <div className="tab-info"><span className="tab-name">Disk & Logs</span></div>
            </button>
          )}
          <button className={`tab-item ${tab === 'security' ? 'active' : ''}`} onClick={() => setTab('security')}>
            <span className="tab-icon">🔒</span>
            <div className="tab-info"><span className="tab-name">Password</span></div>
          </button>
        </div>
        <div className="settings-content">
          {tab === 'disk' && logs && <DiskPanel />}
          {tab === 'security' && <SecurityPanel />}
        </div>
      </div>
    </div>
  )
}

function AdminSettingsPage() {
  const [activeTab, setActiveTab] = useState('ruckus_one')
  const [r1, setR1]               = useState({ region: 'EU', tenant_id: '', client_id: '', client_secret: '' })
  const [saving, setSaving]       = useState(false)
  const [saved, setSaved]         = useState(false)
  const [error, setError]         = useState(null)
  const [testing, setTesting]     = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [r1Enabled, setR1Enabled]   = useState(false)
  const [ulEnabled, setUlEnabled]   = useState(false)
  const [szEnabled, setSzEnabled]   = useState(false)
  const [activePlatform, setActivePlatformState] = useState(null)
  const [platformSwitching, setPlatformSwitching] = useState(false)

  useEffect(() => {
    api.getSettings().then(data => {
      if (data.ruckus_one) setR1(data.ruckus_one)
    }).catch(() => {})
    api.status().then(s => {
      setR1Enabled(!!s.ruckus_last_sync && s.ruckus_clients_cached > 0)
    }).catch(() => {})
    api.getUnleashedSettings().then(s => {
      setUlEnabled(!!s.enabled)
    }).catch(() => {})
    api.getSmartZoneSettings().then(s => {
      setSzEnabled(!!s.enabled)
    }).catch(() => {})
    api.getActivePlatform().then(d => setActivePlatformState(d.active)).catch(() => {})
  }, [])

  // Shared exclusivity sync: whenever ANY platform becomes the active one
  // (whether via the top banner buttons, or by toggling "Enable X
  // integration" + Save within that platform's own settings tab), reflect
  // it immediately everywhere — banner, sidebar status badges, and the
  // other two platforms' own enabled state — rather than only updating the
  // single flag that one code path happens to own.
  const syncPlatformExclusivity = (platform, isEnabled) => {
    if (isEnabled) {
      setActivePlatformState(platform)
      setR1(s => ({ ...s, enabled: platform === 'ruckus_one' }))
      setUlEnabled(platform === 'unleashed')
      setSzEnabled(platform === 'smartzone')
    } else {
      if (platform === 'ruckus_one') setR1(s => ({ ...s, enabled: false }))
      if (platform === 'unleashed') setUlEnabled(false)
      if (platform === 'smartzone') setSzEnabled(false)
    }
  }

  const switchPlatform = async (platform) => {
    setPlatformSwitching(true)
    try {
      await api.setActivePlatform(platform)
      syncPlatformExclusivity(platform, true)
      // Trigger a sync for the newly active platform
      if (platform === 'ruckus_one') await api.getSettings().catch(() => {})
      if (platform === 'smartzone') await api.testSmartZone().catch(() => {})
      if (platform === 'unleashed') await api.testUnleashedConnection?.({}).catch(() => {})
    } finally {
      setPlatformSwitching(false)
    }
  }

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      await api.saveRuckusSettings(r1)
      setSaved(true)
      syncPlatformExclusivity('ruckus_one', r1.enabled)
      setTimeout(() => setSaved(false), 3000)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const result = await api.testRuckusOne()
      if (result.ok) {
        setTestResult({ ok: true })
      } else {
        setTestResult({ ok: false, error: result.error || 'Connection failed' })
      }
    } catch (e) {
      setTestResult({ ok: false, error: e.message })
    } finally {
      setTesting(false)
    }
  }

  const selectedRegion = REGIONS.find(r => r.value === r1.region) || REGIONS[0]

  return (
    <div className="settings-page">
      <div className="settings-header">
        <span className="settings-title">Settings</span>
      </div>

      <div className="platform-selector">
        <span className="platform-selector-label">Active WiFi platform:</span>
        <div className="platform-options">
          {[
            { key: 'ruckus_one', label: 'Ruckus One' },
            { key: 'unleashed',  label: 'Unleashed' },
            { key: 'smartzone',  label: 'SmartZone' },
          ].map(p => (
            <button
              key={p.key}
              className={`platform-btn ${activePlatform === p.key ? 'active' : ''}`}
              onClick={() => switchPlatform(p.key)}
              disabled={platformSwitching}
            >
              {activePlatform === p.key && '● '}{p.label}
            </button>
          ))}
        </div>
        <span className="platform-hint">Only one platform can be active at a time. Switching keeps each configuration saved.</span>
      </div>

      <div className="settings-layout">
        <div className="settings-tabs">
          <button className={`tab-item ${activeTab === 'ruckus_one' ? 'active' : ''}`} onClick={() => setActiveTab('ruckus_one')}>
            <span className="tab-icon">🌐</span>
            <div className="tab-info">
              <span className="tab-name">Ruckus One</span>
              <span className={`tab-status ${r1.enabled ? 'status-active' : 'status-disabled'}`}>
                {r1.enabled ? 'Enabled' : 'Disabled'}
              </span>
            </div>
          </button>
          <button className={`tab-item ${activeTab === 'unleashed' ? 'active' : ''}`} onClick={() => setActiveTab('unleashed')}>
            <span className="tab-icon">📡</span>
            <div className="tab-info">
              <span className="tab-name">Unleashed</span>
              <span className={`tab-status ${ulEnabled ? 'status-active' : 'status-disabled'}`}>
                {ulEnabled ? 'Enabled' : 'Disabled'}
              </span>
            </div>
          </button>
          <button className={`tab-item ${activeTab === 'smartzone' ? 'active' : ''}`} onClick={() => setActiveTab('smartzone')}>
            <span className="tab-icon">🏢</span>
            <div className="tab-info">
              <span className="tab-name">SmartZone</span>
              <span className={`tab-status ${szEnabled ? 'status-active' : 'status-disabled'}`}>
                {szEnabled ? 'Enabled' : 'Disabled'}
              </span>
            </div>
          </button>
          <button className={`tab-item ${activeTab === 'preferences' ? 'active' : ''}`} onClick={() => setActiveTab('preferences')}>
            <span className="tab-icon">🕐</span>
            <div className="tab-info">
              <span className="tab-name">Preferences</span>
            </div>
          </button>
          <button className={`tab-item ${activeTab === 'disk' ? 'active' : ''}`} onClick={() => setActiveTab('disk')}>
            <span className="tab-icon">💾</span>
            <div className="tab-info">
              <span className="tab-name">Disk & Logs</span>
            </div>
          </button>
          <button className={`tab-item ${activeTab === 'security' ? 'active' : ''}`} onClick={() => setActiveTab('security')}>
            <span className="tab-icon">🔒</span>
            <div className="tab-info">
              <span className="tab-name">Password</span>
            </div>
          </button>
          <button className={`tab-item ${activeTab === 'users' ? 'active' : ''}`} onClick={() => setActiveTab('users')}>
            <span className="tab-icon">👥</span>
            <div className="tab-info">
              <span className="tab-name">Users</span>
            </div>
          </button>
          <button className={`tab-item ${activeTab === 'ssl' ? 'active' : ''}`} onClick={() => setActiveTab('ssl')}>
            <span className="tab-icon">🔐</span>
            <div className="tab-info">
              <span className="tab-name">SSL</span>
            </div>
          </button>
        </div>

        <div className="settings-content">
          {activeTab === 'ruckus_one' && (
            <div className="settings-panel">
              <div className="panel-title">Ruckus One API</div>
              <div className="panel-desc">
                Connect to Ruckus One cloud to enrich flow logs with client identity,
                venue, SSID and guest information.
              </div>

              <div className="form-section">
                <div className="form-label">Enable Ruckus One integration</div>
                <label className="toggle-label">
                  <input
                    type="checkbox"
                    checked={r1.enabled || false}
                    onChange={e => setR1(s => ({ ...s, enabled: e.target.checked }))}
                  />
                  <span className="toggle-text">{r1.enabled ? 'Enabled' : 'Disabled'}</span>
                </label>
              </div>

              <div className="form-section">
                <div className="form-label">Region</div>
                <div className="region-selector">
                  {REGIONS.map(r => (
                    <button key={r.value} className={`region-btn ${r1.region === r.value ? 'active' : ''}`} onClick={() => setR1(s => ({ ...s, region: r.value }))}>
                      <span className="region-name">{r.label}</span>
                      <span className="region-url">{r.url}</span>
                    </button>
                  ))}
                </div>
              </div>

              <div className="form-section">
                <div className="form-label">Tenant ID</div>
                <input className="form-input" value={r1.tenant_id} onChange={e => setR1(s => ({ ...s, tenant_id: e.target.value }))} placeholder="44b5983c85034d0e9ae07f474fa9eb55" spellCheck={false} />
                <div className="form-hint">Found in your Ruckus One URL: /t/<strong>tenant_id</strong>/...</div>
              </div>

              <div className="form-row">
                <div className="form-section">
                  <div className="form-label">Client ID</div>
                  <input className="form-input" value={r1.client_id} onChange={e => setR1(s => ({ ...s, client_id: e.target.value }))} placeholder="Client ID" spellCheck={false} />
                </div>
                <div className="form-section">
                  <div className="form-label">Client Secret</div>
                  <input className="form-input" type="password" value={r1.client_secret} onChange={e => setR1(s => ({ ...s, client_secret: e.target.value }))} placeholder="••••••••••••••••" />
                </div>
              </div>

              <div className="form-section">
                <div className="form-label">API Endpoint</div>
                <div className="endpoint-display">
                  https://{selectedRegion.url}/oauth2/token/{r1.tenant_id || '<tenant_id>'}
                </div>
              </div>

              {error && <div className="form-error">⚠ {error}</div>}
              {saved && <div className="form-success">✓ Settings saved — sync triggered</div>}

              <div className="form-actions">
                <button className="btn-ghost" onClick={handleTest} disabled={testing}>{testing ? 'Testing...' : 'Test connection'}</button>
                <button className="btn-primary" onClick={handleSave} disabled={saving}>{saving ? 'Saving...' : 'Save & sync'}</button>
              </div>

              {testResult && (
                <div className={`test-result ${testResult.ok ? 'test-ok' : 'test-fail'}`}>
                  {testResult.ok ? `✓ Connected — authentication successful` : `✗ ${testResult.error}`}
                </div>
              )}
            </div>
          )}

          {activeTab === 'unleashed' && <UnleashedPanel key={activePlatform} onEnabledChange={(v) => syncPlatformExclusivity('unleashed', v)} />}

          {activeTab === "smartzone" && <SmartZonePanel key={activePlatform} onEnabledChange={(v) => syncPlatformExclusivity('smartzone', v)} />}

          {activeTab === 'preferences' && <PreferencesPanel />}
          {activeTab === 'disk' && <DiskPanel />}
          {activeTab === 'security' && <SecurityPanel />}
          {activeTab === 'users' && <UsersPanel />}
          {activeTab === 'ssl' && <SslPanel />}
        </div>
      </div>
    </div>
  )
}
