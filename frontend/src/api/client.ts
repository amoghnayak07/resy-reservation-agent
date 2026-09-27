import { getSessionId } from '../session'
import type {
  ApiErrorBody,
  ChatRequestBody,
  ConfirmBookingBody,
  ConversationOut,
  DeclineBookingBody,
  HealthResponse,
  MessageOut,
  RegionsResponse,
} from './types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL as string

export class ApiError extends Error {
  code: string
  retryAfterSeconds?: number

  constructor(code: string, message: string, retryAfterSeconds?: number) {
    super(message)
    this.code = code
    this.retryAfterSeconds = retryAfterSeconds
  }
}

async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      ...init?.headers,
      'X-Session-Id': getSessionId(),
    },
  })
  if (!response.ok) {
    const body = (await response.json()) as ApiErrorBody
    const retryAfterHeader = response.headers.get('Retry-After')
    throw new ApiError(
      body.error.code,
      body.error.message,
      retryAfterHeader ? Number(retryAfterHeader) : undefined,
    )
  }
  return response
}

export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/health`, { signal })
  if (!response.ok) {
    throw new Error(`Health check failed: ${response.status}`)
  }
  return (await response.json()) as HealthResponse
}

export async function getRegions(): Promise<RegionsResponse> {
  const response = await apiFetch('/api/regions')
  return (await response.json()) as RegionsResponse
}

export async function listConversations(): Promise<ConversationOut[]> {
  const response = await apiFetch('/api/conversations')
  return (await response.json()) as ConversationOut[]
}

export async function getConversationMessages(conversationId: string): Promise<MessageOut[]> {
  const response = await apiFetch(`/api/conversations/${conversationId}/messages`)
  return (await response.json()) as MessageOut[]
}

export async function postChatMessage(body: ChatRequestBody): Promise<Response> {
  return apiFetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// Both return the resumed run as the same SSE stream as /api/chat.
export async function confirmBooking(
  pendingBookingId: string,
  body: ConfirmBookingBody,
): Promise<Response> {
  return apiFetch(`/api/bookings/${pendingBookingId}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function declineBooking(
  pendingBookingId: string,
  body: DeclineBookingBody,
): Promise<Response> {
  return apiFetch(`/api/bookings/${pendingBookingId}/decline`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}
