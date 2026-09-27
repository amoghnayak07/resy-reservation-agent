import type { ChatEvent } from '../api/types'

// Per-turn stream state derived from SSE events: which tools are running (for the
// "Searching Resy…" indicator) and whether a search needed the user's location.

export interface StreamStatus {
  activeTools: Record<string, string> // call_id -> tool name
  locationRequired: boolean
}

export const INITIAL_STREAM_STATUS: StreamStatus = { activeTools: {}, locationRequired: false }

const TOOL_LABELS: Record<string, string> = {
  search_availability: 'Searching Resy…',
  get_venue_details: 'Looking up the restaurant…',
  get_venue_calendar: 'Checking open dates…',
  prepare_booking: 'Preparing your booking…',
  book: 'Booking on Resy…',
}

export function applyStreamEvent(status: StreamStatus, event: ChatEvent): StreamStatus {
  switch (event.event) {
    case 'tool_start':
      return {
        ...status,
        activeTools: { ...status.activeTools, [event.data.call_id]: event.data.name },
      }
    case 'tool_end': {
      const activeTools = { ...status.activeTools }
      delete activeTools[event.data.call_id]
      return { ...status, activeTools }
    }
    case 'location_required':
      return { ...status, locationRequired: true }
    case 'done':
    case 'error':
      return { ...status, activeTools: {} }
    default:
      return status
  }
}

export function activityLabel(status: StreamStatus): string {
  const running = Object.values(status.activeTools)
  if (running.length === 0) return 'Working…'
  return TOOL_LABELS[running[0]] ?? 'Working…'
}
