export interface HealthResponse {
  status: string
  env: string
  version: string
}

export interface ApiErrorBody {
  error: {
    code: string
    message: string
  }
}

// A Resy city slug (e.g. "new-york-ny") from GET /api/regions; the backend resolves it to the
// search center, radius, and timezone.
export interface ChatRequestBody {
  conversation_id?: string
  message: string
  region: string
}

export interface RegionCity {
  slug: string
  name: string
}

export interface RegionCountry {
  code: string
  name: string
  cities: RegionCity[]
}

export interface RegionsResponse {
  countries: RegionCountry[]
}

export interface RegionChangeRequiredEvent {
  city?: string
}

export interface ConversationOut {
  id: string
  title: string | null
  message_count: number
  created_at: string
  updated_at: string
  last_message_at: string | null
}

export interface MessageOut {
  role: 'human' | 'ai'
  content: string
}

export interface MetaEvent {
  conversation_id: string
  trace_id: string
}

export interface TokenEvent {
  text: string
}

export interface ToolStartEvent {
  name: string
  call_id: string
}

export interface ToolEndEvent {
  name: string
  call_id: string
  ok: boolean
  duration_ms: number
}

// Mirrors backend app/agent/tools/book.py card_summary (optional keys are omitted when unknown).
export interface BookingSummary {
  restaurant: string
  address?: string
  date: string
  weekday: string
  time: string
  party_size: number
  seating?: string
  cost: string
  cancellation_policy?: string
  free_cancellation_until?: string
  changes_allowed_until?: string
  hold_expires?: string
  expires_at: string
}

export interface ConfirmationRequiredEvent {
  pending_booking_id: string
  summary: BookingSummary
}

export interface ConfirmBookingBody {
  passcode: string
  region: string
}

export interface DeclineBookingBody {
  region: string
}

export interface UsageEvent {
  model: string
  input_tokens: number
  cached_tokens: number
  output_tokens: number
  cost_usd: number
  latency_ms: number
  ttft_ms: number | null
}

export interface ErrorEvent {
  code: string
  message: string
}

export type ChatEvent =
  | { event: 'meta'; data: MetaEvent }
  | { event: 'token'; data: TokenEvent }
  | { event: 'tool_start'; data: ToolStartEvent }
  | { event: 'tool_end'; data: ToolEndEvent }
  | { event: 'region_change_required'; data: RegionChangeRequiredEvent }
  | { event: 'confirmation_required'; data: ConfirmationRequiredEvent }
  | { event: 'usage'; data: UsageEvent }
  | { event: 'error'; data: ErrorEvent }
  | { event: 'done'; data: Record<string, never> }
