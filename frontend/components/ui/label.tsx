import { cn } from '@/lib/utils'
import { LabelHTMLAttributes } from 'react'

export function Label({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label className={cn('block text-sm font-medium text-gray-700 mb-1', className)} {...props} />
  )
}
