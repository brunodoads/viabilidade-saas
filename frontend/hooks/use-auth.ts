'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { login as apiLogin, ApiError } from '@/lib/api'
import { setToken, clearToken } from '@/lib/auth'

export function useSignIn() {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const router = useRouter()

  async function signIn(email: string, password: string) {
    setLoading(true)
    setError(null)
    try {
      const data = await apiLogin(email, password)
      setToken(data.access_token)
      window.location.href = '/dashboard'
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Erro ao fazer login')
    } finally {
      setLoading(false)
    }
  }

  return { signIn, loading, error }
}

export function useSignOut() {
  const router = useRouter()
  return function signOut() {
    clearToken()
    router.push('/login')
  }
}
