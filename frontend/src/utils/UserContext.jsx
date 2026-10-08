import { createContext, useContext } from 'react'

// The signed-in account: { username, role, must_change_password, ... }
const UserContext = createContext(null)

export const UserProvider = UserContext.Provider
export const useUser = () => useContext(UserContext)
export const isAdmin = (user) => user?.role === 'admin'
// Erase client logs, retention and purge: administrators and logs managers
export const canManageLogs = (user) => ['admin', 'manager'].includes(user?.role)
export const ROLE_LABELS = { admin: 'Administrator', manager: 'Logs manager', viewer: 'Read-only' }
