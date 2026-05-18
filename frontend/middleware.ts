import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

// JWT lives in localStorage — SSR middleware can't read it.
// Auth protection happens client-side in (dashboard)/layout.tsx.
// This middleware only handles basic path redirects.
export function middleware(req: NextRequest) {
  return NextResponse.next()
}

export const config = {
  matcher: ['/((?!api|_next/static|_next/image|favicon.ico).*)'],
}
