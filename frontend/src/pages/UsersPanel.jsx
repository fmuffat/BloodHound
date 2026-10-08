import { useState, useEffect, useCallback } from 'react'
import { api } from '../utils/api'
import { useUser, ROLE_LABELS } from '../utils/UserContext'

const cell = { padding: '8px 10px', borderBottom: '1px solid var(--border)', fontSize: 12, textAlign: 'left' }
const head = { ...cell, fontFamily: 'var(--mono)', fontSize: 10, textTransform: 'uppercase', color: 'var(--text-dim)' }

function fmt(ts) {
  return ts ? new Date(ts).toLocaleString() : 'never'
}

// Accounts (administrators only): create, change role, reset password, delete.
export default function UsersPanel() {
  const me = useUser()
  const [users, setUsers]       = useState([])
  const [newName, setNewName]   = useState('')
  const [newRole, setNewRole]   = useState('viewer')
  const [error, setError]       = useState(null)
  const [secret, setSecret]     = useState(null)   // {username, password} to hand over
  const [busy, setBusy]         = useState(false)

  const load = useCallback(() =>
    api.listUsers().then(d => setUsers(d.items)).catch(e => setError(e.detail || e.message)), [])
  useEffect(() => { load() }, [load])

  const run = async (fn) => {
    setBusy(true); setError(null)
    try { await fn(); await load() }
    catch (e) { setError(e.detail || e.message) }
    finally { setBusy(false) }
  }

  const create = () => run(async () => {
    const r = await api.createUser(newName.trim(), newRole)
    setSecret({ username: r.username, password: r.password })
    setNewName('')
  })
  const changeRole = (u, role) => run(() => api.setUserRole(u.username, role))
  const reset = (u) => {
    if (!window.confirm(`New password for ${u.username}? Their sessions will be closed.`)) return
    run(async () => {
      const r = await api.resetUserPassword(u.username)
      setSecret({ username: r.username, password: r.password })
    })
  }
  const remove = (u) => {
    if (!window.confirm(`Delete the account ${u.username}?`)) return
    run(() => api.deleteUser(u.username))
  }

  return (
    <div className="settings-panel">
      <div className="panel-title">Users &amp; sign-ins</div>
      <div className="panel-desc">
        <strong>Administrator</strong>: everything. <strong>Logs manager</strong>: search,
        investigate, export, erase client logs, retention and purge — no server administration
        (platforms, SSL, preferences, accounts). <strong>Read-only</strong>: search, investigate
        and export only.
        New and reset passwords are random and must be changed at the next sign-in.
      </div>

      {secret && (
        <div className="form-success" style={{ marginBottom: 16 }}>
          Password for <strong>{secret.username}</strong>:{' '}
          <code style={{ fontFamily: 'var(--mono)', userSelect: 'all' }}>{secret.password}</code>
          <div className="form-hint">Give it to the user now — it is not shown again. They will choose their own at sign-in.</div>
          <button className="btn-ghost" style={{ marginTop: 8 }} onClick={() => setSecret(null)}>Done</button>
        </div>
      )}

      <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 20 }}>
        <thead>
          <tr>
            <th style={head}>Account</th><th style={head}>Role</th>
            <th style={head}>Last sign-in</th><th style={head}></th>
          </tr>
        </thead>
        <tbody>
          {users.map(u => {
            const self = u.username === me?.username
            return (
              <tr key={u.username}>
                <td style={cell}>
                  {u.username}{self && <span className="form-hint"> (you)</span>}
                  {u.must_change_password && <div className="form-hint">must choose a password</div>}
                </td>
                <td style={cell}>
                  <select className="form-input" style={{ width: 'auto', padding: '4px 8px' }}
                          value={u.role} disabled={busy || self}
                          onChange={e => changeRole(u, e.target.value)}>
                    {Object.entries(ROLE_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                </td>
                <td style={cell}>{fmt(u.last_login_at)}</td>
                <td style={{ ...cell, whiteSpace: 'nowrap', textAlign: 'right' }}>
                  {!self && <>
                    <button className="btn-ghost" disabled={busy} onClick={() => reset(u)}>Reset password</button>{' '}
                    <button className="btn-danger" disabled={busy} onClick={() => remove(u)}>Delete</button>
                  </>}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      <div className="form-section">
        <div className="form-label">New account</div>
        <div style={{ display: 'flex', gap: 8 }}>
          <input className="form-input" placeholder="username" value={newName}
                 onChange={e => setNewName(e.target.value)} autoComplete="off" />
          <select className="form-input" style={{ width: 'auto' }} value={newRole}
                  onChange={e => setNewRole(e.target.value)}>
            {Object.entries(ROLE_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <button className="btn-primary" disabled={busy || newName.trim().length < 2} onClick={create}>
            Create
          </button>
        </div>
        <div className="form-hint">Letters, digits and . _ @ - (2 to 64 characters).</div>
      </div>

      {error && <div className="form-error">⚠ {error}</div>}

      <SignInLog />
    </div>
  )
}

// Every sign-in attempt (as in sFlow Analytics): who, when, from where, result.
function SignInLog() {
  const [data, setData]             = useState(null)
  const [failedOnly, setFailedOnly] = useState(false)
  const [error, setError]           = useState(null)

  const load = useCallback(() => {
    api.listLogins(failedOnly).then(setData).catch(e => setError(e.detail || e.message))
  }, [failedOnly])
  useEffect(() => { load() }, [load])

  const s = data?.last_24h
  const badge = (ok) => ({
    display: 'inline-block', padding: '1px 8px', borderRadius: 10, fontSize: 11, fontWeight: 600,
    color: ok ? 'var(--green)' : 'var(--red)', background: ok ? 'var(--ok-soft)' : 'var(--bad-soft)',
  })

  return (
    <div style={{ marginTop: 28, paddingTop: 20, borderTop: '1px solid var(--border)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
        <div className="panel-title" style={{ marginBottom: 0 }}>Recent sign-ins</div>
        <span style={{ flex: 1 }} />
        <label className="form-hint" style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 0, cursor: 'pointer' }}>
          <input type="checkbox" checked={failedOnly} onChange={e => setFailedOnly(e.target.checked)} />
          Failed only
        </label>
        <button className="btn-ghost" onClick={load}>Refresh</button>
      </div>
      {s && (
        <div className="form-hint" style={{ marginTop: 0, marginBottom: 12 }}>
          Last 24 h: <strong>{s.total}</strong> sign-in attempts,{' '}
          <strong style={{ color: s.failed ? 'var(--red)' : undefined }}>{s.failed}</strong> failed
          {s.failed > 0 && <> from <strong>{s.failed_clients}</strong> address{s.failed_clients > 1 ? 'es' : ''}</>}.
          {' '}Kept {data.retention_days} days.
        </div>
      )}
      {error && <div className="form-error">⚠ {error}</div>}
      <div style={{ maxHeight: 420, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 6 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              {['When', 'Account', 'From', 'Result', 'Detail'].map(h =>
                <th key={h} style={{ ...head, position: 'sticky', top: 0, background: 'var(--bg-card)' }}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {(data?.items || []).map((e, i) => (
              <tr key={i}>
                <td style={cell}>{fmt(e.at)}</td>
                <td style={cell}>{e.username || <span className="form-hint">(empty)</span>}</td>
                <td style={{ ...cell, fontFamily: 'var(--mono)' }}>{e.client}</td>
                <td style={cell}><span style={badge(e.ok)}>{e.ok ? 'OK' : 'Failed'}</span></td>
                <td style={{ ...cell, color: 'var(--text-dim)' }}>{e.detail}</td>
              </tr>
            ))}
            {data && data.items.length === 0 && (
              <tr><td style={{ ...cell, color: 'var(--text-dim)' }} colSpan={5}>No sign-in recorded yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
