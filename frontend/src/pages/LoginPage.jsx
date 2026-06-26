import { useState } from 'react'
import { api } from '../utils/api'
import './LoginPage.css'

export default function LoginPage({ onLogin }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError]       = useState(null)
  const [loading, setLoading]   = useState(false)

  const handleSubmit = async () => {
    if (!username || !password) {
      setError('Please enter username and password')
      return
    }
    setLoading(true)
    setError(null)
    try {
      await api.login(username, password)
      onLogin(username)
    } catch (e) {
      setError('Invalid username or password')
    } finally {
      setLoading(false)
    }
  }

  const handleKey = (e) => {
    if (e.key === 'Enter') handleSubmit()
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <img src="/bloodhound-logo.png" alt="Bloodhound" className="login-logo" />
        <div className="login-title">BLOODHOUND</div>
        <div className="login-subtitle">WiFi Investigation Platform</div>

        <div className="login-form">
          <div className="login-field">
            <label>Username</label>
            <input
              type="text"
              value={username}
              onChange={e => setUsername(e.target.value)}
              onKeyDown={handleKey}
              placeholder=""
              autoFocus
              autoComplete="username"
            />
          </div>
          <div className="login-field">
            <label>Password</label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              onKeyDown={handleKey}
              placeholder=""
              autoComplete="current-password"
            />
          </div>

          {error && <div className="login-error">{error}</div>}

          <button
            className="login-btn"
            onClick={handleSubmit}
            disabled={loading}
          >
            {loading ? 'Signing in...' : 'Sign in'}
          </button>
        </div>
      </div>
    </div>
  )
}
