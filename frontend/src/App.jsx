import { useState, useEffect, useCallback, useRef } from 'react'
import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import SearchPage from './pages/SearchPage'
import InvestigationPage from './pages/InvestigationPage'
import EventInvestigationPage from './pages/EventInvestigationPage'
import SettingsPage from './pages/SettingsPage'
import LoginPage from './pages/LoginPage'
import ForcePasswordPage from './pages/ForcePasswordPage'
import { PrefsProvider } from './utils/PrefsContext'
import { UserProvider } from './utils/UserContext'
import { api } from './utils/api'

const INACTIVITY_TIMEOUT = 30 * 60 * 1000 // 30 minutes
const WARNING_BEFORE     =  2 * 60 * 1000 //  2 minutes before logout

export default function App() {
  const [user, setUser]         = useState(null)
  const [checking, setChecking] = useState(true)
  const [warning, setWarning]   = useState(false)
  const timerRef   = useRef(null)
  const warnRef    = useRef(null)

  const doLogout = useCallback(() => {
    api.logout().catch(() => {})
    setUser(null)
    setWarning(false)
  }, [])

  const resetTimer = useCallback(() => {
    if (!user) return
    setWarning(false)
    clearTimeout(timerRef.current)
    clearTimeout(warnRef.current)
    warnRef.current  = setTimeout(() => setWarning(true), INACTIVITY_TIMEOUT - WARNING_BEFORE)
    timerRef.current = setTimeout(() => doLogout(), INACTIVITY_TIMEOUT)
  }, [user, doLogout])

  useEffect(() => {
    if (!user) return
    const events = ['mousedown', 'keydown', 'scroll', 'touchstart']
    events.forEach(e => window.addEventListener(e, resetTimer, { passive: true }))
    resetTimer()
    return () => {
      events.forEach(e => window.removeEventListener(e, resetTimer))
      clearTimeout(timerRef.current)
      clearTimeout(warnRef.current)
    }
  }, [user, resetTimer])

  // The account (username, role, must_change_password) comes from the server
  const refreshUser = useCallback(() =>
    api.getMe().then(setUser).catch(() => setUser(null)), [])

  useEffect(() => {
    refreshUser().finally(() => setChecking(false))
  }, [refreshUser])

  if (checking) {
    return (
      <div style={{
        minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'var(--bg)', fontFamily: 'var(--mono)', color: 'var(--text-faint)',
        fontSize: 12, letterSpacing: '0.1em'
      }}>
        LOADING...
      </div>
    )
  }

  if (!user) return <LoginPage onLogin={refreshUser} />

  // New account or reset password: a personal password is required first
  if (user.must_change_password) {
    return <ForcePasswordPage user={user} onDone={refreshUser} onLogout={doLogout} />
  }

  return (
    <UserProvider value={user}>
    <PrefsProvider>
      {warning && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, zIndex: 9999,
          background: 'rgba(249,115,22,0.15)', borderBottom: '1px solid var(--accent)',
          padding: '10px 24px', display: 'flex', alignItems: 'center',
          justifyContent: 'space-between', fontFamily: 'var(--mono)', fontSize: 12,
          color: 'var(--accent)'
        }}>
          <span>You will be logged out in 2 minutes due to inactivity.</span>
          <button onClick={resetTimer} style={{
            background: 'var(--accent)', border: 'none', color: '#fff',
            padding: '4px 16px', borderRadius: 4, cursor: 'pointer',
            fontFamily: 'var(--mono)', fontSize: 12, fontWeight: 600
          }}>
            Stay logged in
          </button>
        </div>
      )}
      <Routes>
        <Route path="/" element={<Layout user={user} onLogout={() => { api.logout(); setUser(null) }} />}>
          <Route index element={<SearchPage />} />
          <Route path="investigate/:mac" element={<InvestigationPage />} />
          <Route path="investigate/:mac/event" element={<EventInvestigationPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
      </Routes>
    </PrefsProvider>
    </UserProvider>
  )
}
