import { useState } from 'react'
import { api } from '../utils/api'
import './LoginPage.css'

// Shown after sign-in when the account must choose its own password
// (new account, password reset by an administrator, default password).
export default function ForcePasswordPage({ user, onDone, onLogout }) {
  const [current, setCurrent] = useState('')
  const [newPwd, setNewPwd]   = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError]     = useState(null)
  const [saving, setSaving]   = useState(false)

  const submit = async () => {
    if (!current || !newPwd) { setError('Please fill in every field'); return }
    if (newPwd.length < 8)   { setError('The new password must be at least 8 characters'); return }
    if (newPwd !== confirm)  { setError('The new passwords do not match'); return }
    setSaving(true)
    setError(null)
    try {
      await api.changePassword({ current_password: current, new_password: newPwd })
      await onDone()
    } catch (e) {
      setError(e.status === 401 ? 'Current password is incorrect' : (e.detail || e.message))
    } finally {
      setSaving(false)
    }
  }

  const onKey = (e) => { if (e.key === 'Enter') submit() }

  return (
    <div className="login-page">
      <div className="login-card">
        <img src="/bloodhound-logo.png" alt="Bloodhound" className="login-logo" />
        <div className="login-title">BLOODHOUND</div>
        <div className="login-subtitle">Choose your password, {user.username}</div>

        <div className="login-form">
          <div className="login-field">
            <label>Current password</label>
            <input type="password" value={current} onChange={e => setCurrent(e.target.value)}
                   onKeyDown={onKey} autoFocus autoComplete="current-password" />
          </div>
          <div className="login-field">
            <label>New password (8 characters minimum)</label>
            <input type="password" value={newPwd} onChange={e => setNewPwd(e.target.value)}
                   onKeyDown={onKey} autoComplete="new-password" />
          </div>
          <div className="login-field">
            <label>New password again</label>
            <input type="password" value={confirm} onChange={e => setConfirm(e.target.value)}
                   onKeyDown={onKey} autoComplete="new-password" />
          </div>

          {error && <div className="login-error">{error}</div>}

          <button className="login-btn" onClick={submit} disabled={saving}>
            {saving ? 'Saving...' : 'Set password and continue'}
          </button>
          <button className="login-btn" onClick={onLogout}
                  style={{ marginTop: 8, background: 'transparent', border: '1px solid var(--border-bright)' }}>
            Sign out
          </button>
        </div>
      </div>
    </div>
  )
}
