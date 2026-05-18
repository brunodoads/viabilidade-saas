'use client'

import { useCallback, useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { Upload, FileSpreadsheet, FileText, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'

const ACCEPTED_TYPES = {
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
  'application/vnd.ms-excel': ['.xls'],
  'text/csv': ['.csv'],
  'application/pdf': ['.pdf'],
}

interface UploadZoneProps {
  onUpload: (file: File) => Promise<void>
  loading?: boolean
}

function FileIcon({ name }: { name: string }) {
  const ext = name.split('.').pop()?.toLowerCase()
  if (ext === 'pdf') return <FileText className="h-8 w-8 text-red-500" />
  return <FileSpreadsheet className="h-8 w-8 text-green-600" />
}

export function UploadZone({ onUpload, loading }: UploadZoneProps) {
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)

  const onDrop = useCallback(
    (accepted: File[], rejected: readonly { errors: readonly { message: string }[] }[]) => {
      setError(null)

      if (rejected.length > 0) {
        setError('Formato inválido. Use XLSX, CSV ou PDF.')
        return
      }

      if (accepted[0]) {
        setFile(accepted[0])
      }
    },
    [],
  )

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: ACCEPTED_TYPES,
    multiple: false,
    maxSize: 50 * 1024 * 1024, // 50MB
  })

  async function handleSubmit() {
    if (!file) return
    setError(null)
    try {
      await onUpload(file)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erro no upload')
    }
  }

  return (
    <div className="space-y-4">
      <div
        {...getRootProps()}
        className={cn(
          'border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-colors',
          isDragActive
            ? 'border-blue-400 bg-blue-50'
            : 'border-gray-300 bg-gray-50 hover:border-blue-300 hover:bg-blue-50/40',
        )}
      >
        <input {...getInputProps()} />
        <Upload className="mx-auto h-10 w-10 text-gray-400 mb-3" />
        <p className="text-sm font-medium text-gray-700">
          {isDragActive ? 'Solte o arquivo aqui' : 'Arraste o catálogo ou clique para selecionar'}
        </p>
        <p className="text-xs text-gray-400 mt-1">XLSX, CSV ou PDF — máximo 50 MB</p>
      </div>

      {file && (
        <div className="flex items-center gap-3 p-3 bg-white border border-gray-200 rounded-lg">
          <FileIcon name={file.name} />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-gray-800 truncate">{file.name}</p>
            <p className="text-xs text-gray-400">{(file.size / 1024 / 1024).toFixed(1)} MB</p>
          </div>
          <button
            onClick={(e) => { e.stopPropagation(); setFile(null) }}
            className="text-gray-400 hover:text-gray-600"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {error && <p className="text-sm text-red-600">{error}</p>}

      <Button
        onClick={handleSubmit}
        disabled={!file || loading}
        loading={loading}
        className="w-full"
        size="lg"
      >
        {loading ? 'Enviando...' : 'Analisar catálogo'}
      </Button>
    </div>
  )
}
