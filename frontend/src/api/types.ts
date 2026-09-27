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

export interface UserLocation {
  lat: number
  lng: number
  accuracy_m: number
}

export interface ChatRequestBody {
  conversation_id?: string
  message: string
  timezone: string
  user_location?: UserLocation
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

export interface ConfirmationRequiredEvent {
  pending_booking_id: string
  summary: string
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
  | { event: 'location_required'; data: Record<string, never> }
  | { event: 'confirmation_required'; data: ConfirmationRequiredEvent }
  | { event: 'usage'; data: UsageEvent }
  | { event: 'error'; data: ErrorEvent }
  | { event: 'done'; data: Record<string, never> }
