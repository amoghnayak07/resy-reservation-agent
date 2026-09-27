import { describe, expect, it } from 'vitest'
import type { RegionCountry } from './api/types'
import { citiesFor, loadRegion, saveRegion, toSelectedRegion, validateRegion } from './region'

const COUNTRIES: RegionCountry[] = [
  { code: 'ES', name: 'Spain', cities: [{ slug: 'a-coruna-spain', name: 'A Coruña, Spain' }] },
  {
    code: 'US',
    name: 'United States',
    cities: [
      { slug: 'los-angeles-ca', name: 'Los Angeles' },
      { slug: 'new-york-ny', name: 'New York' },
    ],
  },
]

function memoryStore() {
  const data = new Map<string, string>()
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
  }
}

describe('region storage', () => {
  it('round-trips the selected region', () => {
    const store = memoryStore()
    const region = toSelectedRegion(COUNTRIES[1], COUNTRIES[1].cities[0])
    saveRegion(region, store)
    expect(loadRegion(store)).toEqual({
      slug: 'los-angeles-ca',
      name: 'Los Angeles',
      countryCode: 'US',
      countryName: 'United States',
    })
  })

  it('ignores missing or malformed values', () => {
    const store = memoryStore()
    expect(loadRegion(store)).toBeNull()
    store.setItem('resy-agent.region', '{not json')
    expect(loadRegion(store)).toBeNull()
    store.setItem('resy-agent.region', JSON.stringify({ slug: 'x' }))
    expect(loadRegion(store)).toBeNull()
  })
})

describe('validateRegion', () => {
  const ny = { slug: 'new-york-ny', name: 'NYC', countryCode: 'US', countryName: 'USA' }

  it('keeps a region still in the city list, refreshing its names', () => {
    expect(validateRegion(ny, COUNTRIES)).toEqual({
      ...ny,
      name: 'New York',
      countryName: 'United States',
    })
  })

  it('drops a region no longer in the list so the picker reopens', () => {
    expect(validateRegion({ ...ny, slug: 'gone-city' }, COUNTRIES)).toBeNull()
    expect(validateRegion(null, COUNTRIES)).toBeNull()
  })
})

describe('citiesFor', () => {
  it('lists only the chosen country', () => {
    expect(citiesFor(COUNTRIES, 'ES').map((c) => c.slug)).toEqual(['a-coruna-spain'])
    expect(citiesFor(COUNTRIES, null)).toEqual([])
  })
})
