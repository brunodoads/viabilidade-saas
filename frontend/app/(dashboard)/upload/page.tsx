'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { UploadZone } from '@/components/upload-zone'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { uploadCatalog, ApiError } from '@/lib/api'
import { ArrowLeft } from 'lucide-react'
import Link from 'next/link'

export default function UploadPage() {
  const router = useRouter()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleUpload(file: File) {
    setLoading(true)
    setError(null)
    try {
      const catalog = await uploadCatalog(file)
      router.push(`/catalogs/${catalog.id}`)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Erro no upload. Tente novamente.')
      setLoading(false)
    }
  }

  return (
    <div className="max-w-xl mx-auto space-y-6">
      {/* Back */}
      <Link
        href="/dashboard"
        className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700"
      >
        <ArrowLeft className="h-4 w-4" />
        Voltar
      </Link>

      <div>
        <h1 className="text-xl font-bold text-gray-900">Novo catálogo</h1>
        <p className="text-sm text-gray-500 mt-1">
          Envie um catálogo de fornecedor para análise automática de viabilidade
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Selecionar arquivo</CardTitle>
        </CardHeader>
        <CardContent>
          <UploadZone onUpload={handleUpload} loading={loading} />
          {error && (
            <p className="mt-3 text-sm text-red-600">{error}</p>
          )}
        </CardContent>
      </Card>

      {/* Info */}
      <div className="text-xs text-gray-400 space-y-1 px-1">
        <p>• Formatos aceitos: XLSX, CSV, PDF</p>
        <p>• Tamanho máximo: 50 MB</p>
        <p>• O processamento leva entre 2 e 10 minutos dependendo do tamanho</p>
      </div>
    </div>
  )
}
