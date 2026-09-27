import { useCallback, useEffect, useEffectEvent, useState } from 'react'
import { getRegions } from '../api/client'
import type { RegionCountry } from '../api/types'
import { loadRegion, saveRegion, validateRegion } from '../region'
import type { SelectedRegion } from '../region'

export type RegionsState = 'loading' | 'ready' | 'error'

// The selected region (localStorage) and the city list for the picker (GET /api/regions).
// Loads once the backend is awake (`enabled`); the picker opens by itself until a valid region
// is chosen.
export function useRegion(enabled: boolean) {
  const [countries, setCountries] = useState<RegionCountry[]>([])
  const [state, setState] = useState<RegionsState>('loading')
  const [region, setRegion] = useState<SelectedRegion | null>(() => loadRegion())
  const [pickerOpen, setPickerOpen] = useState(false)

  const fetchRegions = useEffectEvent(async () => {
    try {
      const { countries: list } = await getRegions()
      setCountries(list)
      setRegion((current) => validateRegion(current, list))
      setState('ready')
    } catch {
      setState('error')
    }
  })

  useEffect(() => {
    if (!enabled || state !== 'loading') return
    const timer = setTimeout(() => void fetchRegions(), 0)
    return () => clearTimeout(timer)
  }, [enabled, state])

  const retry = useCallback(() => setState('loading'), [])

  const select = useCallback((next: SelectedRegion) => {
    saveRegion(next)
    setRegion(next)
    setPickerOpen(false)
  }, [])

  // The backend no longer recognizes the stored region (422 unknown_region).
  const invalidate = useCallback(() => setRegion(null), [])

  return {
    countries,
    state,
    region,
    pickerOpen: pickerOpen || (state !== 'loading' && region === null),
    openPicker: () => setPickerOpen(true),
    closePicker: () => setPickerOpen(false),
    select,
    invalidate,
    retry,
  }
}
