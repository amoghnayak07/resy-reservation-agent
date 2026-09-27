import type { RegionCity, RegionCountry } from './api/types'

// The user's selected Resy region, kept in localStorage across visits (not identifying; the
// guest session ID stays in sessionStorage). Only the slug is sent to the backend.

export interface SelectedRegion {
  slug: string
  name: string
  countryCode: string
  countryName: string
}

const STORAGE_KEY = 'resy-agent.region'

type KeyValueStore = Pick<Storage, 'getItem' | 'setItem'>

function isSelectedRegion(value: unknown): value is SelectedRegion {
  if (typeof value !== 'object' || value === null) return false
  const v = value as Record<string, unknown>
  return ['slug', 'name', 'countryCode', 'countryName'].every((k) => typeof v[k] === 'string')
}

export function loadRegion(store: KeyValueStore = localStorage): SelectedRegion | null {
  try {
    const parsed: unknown = JSON.parse(store.getItem(STORAGE_KEY) ?? 'null')
    return isSelectedRegion(parsed) ? parsed : null
  } catch {
    return null
  }
}

export function saveRegion(region: SelectedRegion, store: KeyValueStore = localStorage): void {
  store.setItem(STORAGE_KEY, JSON.stringify(region))
}

// The stored region re-checked against the current city list: a city Resy dropped (or a
// renamed slug) means the picker opens again.
export function validateRegion(
  region: SelectedRegion | null,
  countries: RegionCountry[],
): SelectedRegion | null {
  if (!region) return null
  const country = countries.find((c) => c.code === region.countryCode)
  const city = country?.cities.find((c) => c.slug === region.slug)
  return country && city ? { ...region, name: city.name, countryName: country.name } : null
}

export function citiesFor(countries: RegionCountry[], countryCode: string | null): RegionCity[] {
  return countries.find((c) => c.code === countryCode)?.cities ?? []
}

export function toSelectedRegion(country: RegionCountry, city: RegionCity): SelectedRegion {
  return { slug: city.slug, name: city.name, countryCode: country.code, countryName: country.name }
}
