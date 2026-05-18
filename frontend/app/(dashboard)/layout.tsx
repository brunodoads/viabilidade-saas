'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { isAuthenticated } from '@/lib/auth'
import { useSignOut } from '@/hooks/use-auth'
import { BarChart3, LogOut, Upload } from 'lucide-react'
import Link from 'next/link'

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const signOut = useSignOut()

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace('/login')
    }
  }, [router])

  return (
    <div className="min-h-screen flex flex-col">
      {/* Navbar */}
      <header className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 h-14 flex items-center justify-between">
          <Link href="/dashboard" className="flex items-center gap-2 text-gray-900">
            <div className="flex items-center justify-center w-8 h-8 bg-blue-600 rounded-lg">
              <BarChart3 className="h-4 w-4 text-white" />
            </div>
            <span className="font-semibold text-sm">Viabilidade</span>
          </Link>

          <div className="flex items-center gap-2">
            <Link
              href="/upload"
              className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:text-blue-600 px-3 py-1.5 rounded-md hover:bg-blue-50 transition-colors"
            >
              <Upload className="h-4 w-4" />
              Novo catálogo
            </Link>
            <button
              onClick={signOut}
              className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700 px-3 py-1.5 rounded-md hover:bg-gray-100 transition-colors"
            >
              <LogOut className="h-4 w-4" />
              Sair
            </button>
          </div>
        </div>
      </header>

      {/* Main */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 py-8">
        {children}
      </main>
    </div>
  )
}
