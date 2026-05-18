'use client'

import { useState, FormEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useSignIn } from '@/hooks/use-auth'
import { BarChart3 } from 'lucide-react'

export default function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const { signIn, loading, error } = useSignIn()

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    await signIn(email, password)
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="w-full max-w-sm">
        {/* Logo / Brand */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-12 h-12 bg-blue-600 rounded-xl mb-4">
            <BarChart3 className="h-6 w-6 text-white" />
          </div>
          <h1 className="text-2xl font-bold text-gray-900">Viabilidade</h1>
          <p className="text-sm text-gray-500 mt-1">Inteligência comercial para seu catálogo</p>
        </div>

        {/* Card */}
        <div className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm space-y-5">
          <h2 className="text-base font-semibold text-gray-800">Entrar na conta</h2>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <Label htmlFor="email">E-mail</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="seu@email.com"
                required
              />
            </div>
            <div>
              <Label htmlFor="password">Senha</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                required
              />
            </div>

            {error && (
              <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
                {error}
              </div>
            )}

            <Button type="submit" loading={loading} className="w-full" size="lg">
              {loading ? 'Entrando...' : 'Entrar'}
            </Button>
          </form>
        </div>

        <p className="text-center text-xs text-gray-400 mt-6">
          MVP — Análise de viabilidade de produtos
        </p>
      </div>
    </div>
  )
}
