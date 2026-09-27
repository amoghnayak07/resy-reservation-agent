import type { UserLocation } from './api/types'

// Location is requested only when the user taps "Use my location", kept in memory for the
// tab (never stored), and re-requested when older than 10 minutes. The backend rounds the
// coordinates and never shows them to the LLM.

export type LocationStatus = 'off' | 'requesting' | 'on' | 'denied' | 'unavailable'

export interface StoredLocation {
  location: UserLocation
  obtainedAt: number
}

export type LocationResult =
  { status: 'on'; value: StoredLocation } | { status: 'denied' } | { status: 'unavailable' }

export const LOCATION_MAX_AGE_MS = 10 * 60 * 1000

export const GEOLOCATION_OPTIONS: PositionOptions = {
  enableHighAccuracy: false,
  timeout: 8000,
  maximumAge: 300000,
}

// GeolocationPositionError.PERMISSION_DENIED; the other codes (unavailable, timeout) are
// retryable.
const PERMISSION_DENIED = 1

export function requestUserLocation(
  geolocation: Geolocation | undefined,
  now: () => number = Date.now,
): Promise<LocationResult> {
  if (!geolocation) return Promise.resolve({ status: 'unavailable' })
  return new Promise((resolve) => {
    geolocation.getCurrentPosition(
      (position) =>
        resolve({
          status: 'on',
          value: {
            location: {
              lat: position.coords.latitude,
              lng: position.coords.longitude,
              accuracy_m: position.coords.accuracy,
            },
            obtainedAt: now(),
          },
        }),
      (error) => resolve({ status: error.code === PERMISSION_DENIED ? 'denied' : 'unavailable' }),
      GEOLOCATION_OPTIONS,
    )
  })
}

export function isStale(stored: StoredLocation, now: number): boolean {
  return now - stored.obtainedAt > LOCATION_MAX_AGE_MS
}
