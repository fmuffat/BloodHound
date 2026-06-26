import { createContext, useContext, useState, useEffect } from 'react'
import { api } from '../utils/api'

const PrefsContext = createContext({ timezone: 'Europe/Paris', clock: '24h' })

export function PrefsProvider({ children }) {
  const [prefs, setPrefs] = useState({ timezone: 'Europe/Paris', clock: '24h' })

  useEffect(() => {
    api.getPreferences().then(setPrefs).catch(() => {})
  }, [])

  const save = async (newPrefs) => {
    const merged = { ...prefs, ...newPrefs }
    setPrefs(merged)
    await api.savePreferences(merged)
  }

  return (
    <PrefsContext.Provider value={{ ...prefs, save }}>
      {children}
    </PrefsContext.Provider>
  )
}

export const usePrefs = () => useContext(PrefsContext)
