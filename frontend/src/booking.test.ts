import { describe, expect, it } from 'vitest'
import type { BookingSummary } from './api/types'
import { cardAfterError, formatCountdown, openCard, secondsLeft, supersede } from './booking'

const SUMMARY: BookingSummary = {
  restaurant: 'Test',
  date: '2026-10-22',
  weekday: 'Thursday',
  time: '19:00',
  party_size: 2,
  cost: 'free',
  expires_at: '2026-10-20T18:10:00+00:00',
}

describe('countdown', () => {
  it('counts down to zero and never below', () => {
    const now = Date.parse('2026-10-20T18:00:00Z')
    expect(secondsLeft(SUMMARY.expires_at, now)).toBe(600)
    expect(secondsLeft(SUMMARY.expires_at, now + 700_000)).toBe(0)
  })

  it('formats minutes and seconds', () => {
    expect(formatCountdown(600)).toBe('10:00')
    expect(formatCountdown(65)).toBe('1:05')
  })
})

describe('cardAfterError', () => {
  const submitting = { ...openCard('b1', SUMMARY), status: 'submitting' as const }

  it('keeps the card open on a wrong passcode so the user can retry', () => {
    const card = cardAfterError(submitting, 'invalid_passcode', "That passcode isn't right.")
    expect(card.status).toBe('open')
    expect(card.error).toBe("That passcode isn't right.")
  })

  it('makes the card read-only when expired or already handled', () => {
    expect(cardAfterError(submitting, 'expired', 'gone').status).toBe('closed')
    const handled = cardAfterError(submitting, 'already_handled', 'Already handled.')
    expect(handled).toMatchObject({ status: 'closed', note: 'Already handled.' })
  })
})

describe('supersede', () => {
  it('closes only open cards', () => {
    expect(supersede(openCard('b1', SUMMARY)).status).toBe('closed')
    const confirmed = { ...openCard('b1', SUMMARY), status: 'confirmed' as const }
    expect(supersede(confirmed)).toBe(confirmed)
  })
})
