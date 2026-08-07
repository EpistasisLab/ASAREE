import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { ApiError, authApi, getStoredAccessToken, setStoredAccessToken } from '@/api/client'
import type { LoginRequest, RegisterRequest, User } from '@/types/auth'
import { AuthContext } from './authContextValue'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const refreshUser = useCallback(async () => {
    if (!getStoredAccessToken()) {
      setUser(null)
      return
    }
    try {
      setUser(await authApi.getMe())
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setStoredAccessToken(null)
        setUser(null)
      } else {
        throw err
      }
    }
  }, [])

  useEffect(() => {
    refreshUser().finally(() => setIsLoading(false))
  }, [refreshUser])

  const login = useCallback(async (data: LoginRequest) => {
    const result = await authApi.login(data)
    setStoredAccessToken(result.access_token)
    setUser(result.user)
  }, [])

  const register = useCallback(async (data: RegisterRequest) => {
    await authApi.register(data)
  }, [])

  const logout = useCallback(async () => {
    try {
      await authApi.logout()
    } catch {
      // A logout call failing server-side (e.g. the token already expired)
      // shouldn't stop the client from forgetting its own credentials.
    }
    setStoredAccessToken(null)
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider
      value={{ user, isAuthenticated: user !== null, isLoading, login, register, logout, refreshUser }}
    >
      {children}
    </AuthContext.Provider>
  )
}
