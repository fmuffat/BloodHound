import { Outlet, NavLink } from 'react-router-dom'
import { useState, useEffect } from 'react'
import { api } from '../utils/api'
import { ROLE_LABELS } from '../utils/UserContext'
import './Layout.css'

export default function Layout({ user, onLogout }) {
  const [status, setStatus] = useState(null)
  const [disk, setDisk]     = useState(null)

  useEffect(() => {
    api.status().then(setStatus).catch(() => {})
    api.getDiskUsage().then(setDisk).catch(() => {})
    const t1 = setInterval(() => api.status().then(setStatus).catch(() => {}), 30000)
    const t2 = setInterval(() => api.getDiskUsage().then(setDisk).catch(() => {}), 60000)
    return () => { clearInterval(t1); clearInterval(t2) }
  }, [])

  const diskPct  = disk?.disk_used_pct || 0
  const diskWarn = diskPct >= 80 && diskPct < 90
  const diskCrit = diskPct >= 90

  return (
    <div className="layout">
      {/* Disk alert banner */}
      {(diskWarn || diskCrit) && (
        <div className={`disk-alert ${diskCrit ? 'disk-alert-crit' : 'disk-alert-warn'}`}>
          {diskCrit ? '🔴' : '🟡'}
          {' '}Disk usage: <strong>{diskPct}%</strong>
          {' '}— {disk.disk_free_gb} GB free.
          {diskCrit
            ? ' Critical — purge logs or expand disk immediately.'
            : ' Warning — consider purging old logs in Settings → System.'}
        </div>
      )}

      <aside className="sidebar">
        <div className="sidebar-logo">
          <img src="/bloodhound-logo.webp" alt="Bloodhound" className="logo-img" />
          <div className="logo-title">BLOODHOUND</div>
        </div>

        <nav className="sidebar-nav">
          <NavLink to="/" end className={({isActive}) => isActive ? 'nav-item active' : 'nav-item'}>
            <span className="nav-icon">⌕</span>
            <span>Search</span>
          </NavLink>
          <NavLink to="/settings" className={({isActive}) => isActive ? 'nav-item active' : 'nav-item'}>
            <span className="nav-icon">⚙</span>
            <span>Settings</span>
          </NavLink>
        </nav>

        <div className="sidebar-status">
          <div className="status-title">SYSTEM</div>
          {status ? (
            <>
              <div className="status-row">
                <span className="status-label">Clients cached</span>
                <span className="status-value">{status.ruckus_clients_cached}</span>
              </div>
              <div className="status-row">
                <span className="status-label">DNS cached</span>
                <span className="status-value">{status.dns_cached}</span>
              </div>
              <div className="status-row">
                <span className="status-label">DNS pending</span>
                <span className="status-value">{status.dns_pending}</span>
              </div>
              {status.ruckus_last_sync && (
                <div className="status-sync">
                  Last sync<br/>
                  {new Date(status.ruckus_last_sync).toLocaleTimeString()}
                </div>
              )}
            </>
          ) : (
            <div className="status-offline">Backend offline</div>
          )}
        </div>

        {/* Disk usage */}
        {disk && (
          <div className="sidebar-disk">
            <div className="status-title">DISK</div>
            <div className="disk-bar-wrapper">
              <div
                className="disk-bar"
                style={{
                  width: `${diskPct}%`,
                  background: diskCrit ? 'var(--red)' : diskWarn ? 'var(--yellow, #f59e0b)' : 'var(--green, #22c55e)'
                }}
              />
            </div>
            <div className="disk-pct" style={{
              color: diskCrit ? 'var(--red)' : diskWarn ? 'var(--yellow, #f59e0b)' : 'var(--text-dim)'
            }}>
              {diskPct}% — {disk.disk_free_gb} GB free
            </div>
          </div>
        )}
      </aside>

      <main className="main-content">
        {/* Top bar with user + logout */}
        <div className="topbar">
          <div className="topbar-user">
            <span className="topbar-username">{user.username}</span>
            {user.role !== 'admin' && <span className="topbar-role">{ROLE_LABELS[user.role] || user.role}</span>}
            <button className="btn-logout" onClick={onLogout}>
              Sign out
            </button>
          </div>
        </div>
        <div className="main-inner">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
