import { useCallback, useRef, useState } from 'react'
import type { UserLocation } from '../api/types'
import { isStale, requestUserLocation } from '../location'
import type { LocationStatus, StoredLocation } from '../location'

function browserGeolocation(): Geolocation | undefined {
  return typeof navigator !== 'undefined' ? navigator.geolocation : undefined
}

// Device location for this tab only: in memory (never sessionStorage), requested only on
// the user's tap, refreshed when older than 10 minutes.
export function useLocation() {
  const [status, setStatus] = useState<LocationStatus>('off')
  const storedRef = useRef<StoredLocation | null>(null)

  const enable = useCallback(async () => {
    setStatus('requesting')
    const result = await requestUserLocation(browserGeolocation())
    if (result.status === 'on') {
      storedRef.current = result.value
      setStatus('on')
    } else {
      storedRef.current = null
      setStatus(result.status)
    }
  }, [])

  const disable = useCallback(() => {
    storedRef.current = null
    setStatus('off')
  }, [])

  // The location to attach to a chat request, or undefined when location is off.
  const getForRequest = useCallback(async (): Promise<UserLocation | undefined> => {
    const stored = storedRef.current
    if (!stored) return undefined
    if (!isStale(stored, Date.now())) return stored.location
    const result = await requestUserLocation(browserGeolocation())
    if (result.status === 'on') {
      storedRef.current = result.value
      return result.value.location
    }
    storedRef.current = null
    setStatus(result.status)
    return undefined
  }, [])

  return { status, enable, disable, getForRequest }
}
