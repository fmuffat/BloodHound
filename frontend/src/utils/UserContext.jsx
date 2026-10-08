import { createContext, useContext } from 'react'

// The signed-in account: { username, role, must_change_password, ... }
const UserContext = createContext(null)

export const UserProvider = UserContext.Provider
export const useUser = () => useContext(UserContext)
export const isAdmin = (user) => user?.role === 'admin'
