import type { ChatRequestBody } from './types'

// Every chat request carries the selected region (a Resy city slug); nothing else about the
// user's location or timezone is sent.
export function buildChatRequest(
  message: string,
  region: string,
  conversationId: string | undefined,
): ChatRequestBody {
  return { conversation_id: conversationId, message, region }
}
