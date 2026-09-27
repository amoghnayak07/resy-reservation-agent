import { describe, expect, it } from 'vitest'
import { GEOLOCATION_OPTIONS, isStale, LOCATION_MAX_AGE_MS, requestUserLocation } from './location'

type Outcome =
  { coords: { latitude: number; longitude: number; accuracy: number } } | { code: number }

// Minimal navigator.geolocation stand-in that answers every request with `outcome`.
function fakeGeolocation(outcome: Outcome) {
  const calls: (PositionOptions | undefined)[] = []
  const geolocation = {
    getCurrentPosition(
      success: PositionCallback,
      error?: PositionErrorCallback | null,
      options?: PositionOptions,
    ) {
      calls.push(options)
      if ('coords' in outcome) success(outcome as unknown as GeolocationPosition)
      else error?.(outcome as unknown as GeolocationPositionError)
    },
  } as unknown as Geolocation
  return { geolocation, calls }
}

describe('requestUserLocation', () => {
  it('returns the position when granted, with the stage 6 options', async () => {
    const { geolocation, calls } = fakeGeolocation({
      coords: { latitude: 40.7128, longitude: -74.006, accuracy: 25 },
    })
    const result = await requestUserLocation(geolocation, () => 1000)
    expect(result).toEqual({
      status: 'on',
      value: { location: { lat: 40.7128, lng: -74.006, accuracy_m: 25 }, obtainedAt: 1000 },
    })
    expect(calls).toEqual([GEOLOCATION_OPTIONS])
    expect(GEOLOCATION_OPTIONS).toEqual({
      enableHighAccuracy: false,
      timeout: 8000,
      maximumAge: 300000,
    })
  })

  it('maps a permission error to denied', async () => {
    const { geolocation } = fakeGeolocation({ code: 1 })
    expect(await requestUserLocation(geolocation)).toEqual({ status: 'denied' })
  })

  it('maps timeout and position-unavailable errors to unavailable (retryable)', async () => {
    expect(await requestUserLocation(fakeGeolocation({ code: 3 }).geolocation)).toEqual({
      status: 'unavailable',
    })
    expect(await requestUserLocation(fakeGeolocation({ code: 2 }).geolocation)).toEqual({
      status: 'unavailable',
    })
  })

  it('is unavailable when the browser has no geolocation', async () => {
    expect(await requestUserLocation(undefined)).toEqual({ status: 'unavailable' })
  })
})

describe('isStale', () => {
  it('refreshes locations older than 10 minutes', () => {
    const stored = { location: { lat: 0, lng: 0, accuracy_m: 1 }, obtainedAt: 0 }
    expect(isStale(stored, LOCATION_MAX_AGE_MS)).toBe(false)
    expect(isStale(stored, LOCATION_MAX_AGE_MS + 1)).toBe(true)
  })
})
