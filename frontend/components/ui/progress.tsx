import { cn } from '@/lib/utils'

interface ProgressProps {
  value: number
  className?: string
}

export function Progress({ value, className }: ProgressProps) {
  const pct = Math.min(100, Math.max(0, value))
  return (
    <div className={cn('h-2 w-full bg-gray-100 rounded-full overflow-hidden', className)}>
      <div
        className="h-full bg-blue-500 rounded-full transition-all duration-500"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}
