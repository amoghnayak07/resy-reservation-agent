import type { BookingSummary } from './api/types'

// Confirmation card state (stage 10). The card is the user's consent step: nothing is booked
// until they confirm here with the passcode. Pure logic so it can be unit-tested.

export type CardStatus = 'open' | 'submitting' | 'confirmed' | 'declined' | 'closed'

export interface ConfirmationCardState {
  pendingBookingId: string
  summary: BookingSummary
  status: CardStatus
  error?: string // shown while the card stays open (e.g. wrong passcode)
  note?: string // shown once the card is read-only
}

export function openCard(pendingBookingId: string, summary: BookingSummary): ConfirmationCardState {
  return { pendingBookingId, summary, status: 'open' }
}

export function secondsLeft(expiresAt: string, nowMs: number): number {
  return Math.max(0, Math.floor((Date.parse(expiresAt) - nowMs) / 1000))
}

export function formatCountdown(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`
}

// Card after the confirm/decline request was refused (HTTP error code from the API).
export function cardAfterError(
  card: ConfirmationCardState,
  code: string,
  message: string,
): ConfirmationCardState {
  switch (code) {
    case 'invalid_passcode':
    case 'rate_limited':
    case 'network_error':
    case 'daily_budget_reached':
      return { ...card, status: 'open', error: message }
    case 'expired':
      return { ...card, status: 'closed', note: 'This hold expired. Nothing was booked.' }
    default: // already_handled, not_found, …
      return { ...card, status: 'closed', note: message }
  }
}

// A new chat message while the card is open declines it on the server.
export function supersede(card: ConfirmationCardState): ConfirmationCardState {
  if (card.status !== 'open') return card
  return { ...card, status: 'closed', note: 'Not booked: you sent a new message.' }
}
