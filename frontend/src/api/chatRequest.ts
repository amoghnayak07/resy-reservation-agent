import type { ChatRequestBody, UserLocation } from './types'

// `user_location` is attached only when the user has turned location on.
export function buildChatRequest(
  message: string,
  timezone: string,
  conversationId: string | undefined,
  location: UserLocation | undefined,
): ChatRequestBody {
  const body: ChatRequestBody = { conversation_id: conversationId, message, timezone }
  if (location) body.user_location = location
  return body
}
