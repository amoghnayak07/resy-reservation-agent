import { useCallback, useState } from 'react'
import { buildChatRequest } from '../api/chatRequest'
import {
  ApiError,
  confirmBooking,
  declineBooking,
  getConversationMessages,
  postChatMessage,
} from '../api/client'
import { parseSSEStream } from '../api/sse'
import type { UsageEvent } from '../api/types'
import { cardAfterError, openCard, supersede } from '../booking'
import type { ConfirmationCardState } from '../booking'
import { applyStreamEvent, INITIAL_STREAM_STATUS } from './streamStatus'
import type { StreamStatus } from './streamStatus'

export interface ChatMessage {
  id: string
  role: 'human' | 'ai'
  content: string
  usage?: UsageEvent
  confirmation?: ConfirmationCardState
}

export interface ChatErrorState {
  code: string
  message: string
  retryAfterSeconds?: number
}

function toErrorState(err: unknown): ChatErrorState {
  if (err instanceof ApiError) {
    return { code: err.code, message: err.message, retryAfterSeconds: err.retryAfterSeconds }
  }
  return { code: 'network_error', message: 'Could not reach the server.' }
}

// `region` is the selected Resy city slug; chat and booking requests need it. When the backend
// no longer recognizes it (422 unknown_region), `onUnknownRegion` lets the page reopen the picker.
export function useChat(
  region: string | undefined,
  onTurnComplete?: () => void,
  onUnknownRegion?: () => void,
) {
  const [conversationId, setConversationId] = useState<string | undefined>(undefined)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [error, setError] = useState<ChatErrorState | null>(null)
  const [streamStatus, setStreamStatus] = useState<StreamStatus>(INITIAL_STREAM_STATUS)

  const updateMessage = useCallback((id: string, update: (m: ChatMessage) => ChatMessage) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? update(m) : m)))
  }, [])

  // Reads one SSE run (a chat turn, or a resumed booking) into the assistant message `assistantId`.
  const consumeStream = useCallback(
    async (response: Response, assistantId: string) => {
      if (response.body) {
        for await (const event of parseSSEStream(response.body)) {
          setStreamStatus((prev) => applyStreamEvent(prev, event))
          switch (event.event) {
            case 'meta':
              setConversationId(event.data.conversation_id)
              break
            case 'token':
              updateMessage(assistantId, (m) => ({ ...m, content: m.content + event.data.text }))
              break
            case 'confirmation_required':
              updateMessage(assistantId, (m) => ({
                ...m,
                confirmation: openCard(event.data.pending_booking_id, event.data.summary),
              }))
              break
            case 'usage':
              updateMessage(assistantId, (m) => ({ ...m, usage: event.data }))
              break
            case 'error':
              setError({ code: event.data.code, message: event.data.message })
              break
            case 'done':
              setIsStreaming(false)
              break
          }
        }
      }
      setIsStreaming(false)
      onTurnComplete?.()
    },
    [onTurnComplete, updateMessage],
  )

  const reportError = useCallback(
    (err: unknown) => {
      const state = toErrorState(err)
      if (state.code === 'unknown_region') onUnknownRegion?.()
      setError(state)
    },
    [onUnknownRegion],
  )

  const sendMessage = useCallback(
    async (text: string) => {
      if (!region) return
      setError(null)
      setStreamStatus(INITIAL_STREAM_STATUS)
      const humanMessage: ChatMessage = { id: crypto.randomUUID(), role: 'human', content: text }
      const assistantId = crypto.randomUUID()
      const assistantMessage: ChatMessage = { id: assistantId, role: 'ai', content: '' }
      setMessages((prev) => [
        ...prev.map((m) =>
          m.confirmation ? { ...m, confirmation: supersede(m.confirmation) } : m,
        ),
        humanMessage,
        assistantMessage,
      ])
      setIsStreaming(true)

      let response
      try {
        response = await postChatMessage(buildChatRequest(text, region, conversationId))
      } catch (err) {
        setMessages((prev) => prev.filter((m) => m.id !== assistantId))
        setIsStreaming(false)
        reportError(err)
        onTurnComplete?.()
        return
      }
      await consumeStream(response, assistantId)
    },
    [conversationId, region, onTurnComplete, reportError, consumeStream],
  )

  // Confirm (with passcode) or decline the card on message `messageId`; the server resumes the
  // paused booking and streams the outcome into a new assistant message.
  const respondToConfirmation = useCallback(
    async (messageId: string, card: ConfirmationCardState, passcode?: string) => {
      if (!region) return
      const approved = passcode !== undefined
      setError(null)
      setStreamStatus(INITIAL_STREAM_STATUS)
      updateMessage(messageId, (m) =>
        m.confirmation ? { ...m, confirmation: { ...card, status: 'submitting' } } : m,
      )
      setIsStreaming(true)

      let response
      try {
        response = approved
          ? await confirmBooking(card.pendingBookingId, { passcode, region })
          : await declineBooking(card.pendingBookingId, { region })
      } catch (err) {
        const { code, message } = toErrorState(err)
        if (code === 'unknown_region') onUnknownRegion?.()
        updateMessage(messageId, (m) => ({
          ...m,
          confirmation: cardAfterError(card, code, message),
        }))
        setIsStreaming(false)
        return
      }

      updateMessage(messageId, (m) => ({
        ...m,
        confirmation: { ...card, status: approved ? 'confirmed' : 'declined' },
      }))
      const assistantId = crypto.randomUUID()
      setMessages((prev) => [...prev, { id: assistantId, role: 'ai', content: '' }])
      await consumeStream(response, assistantId)
    },
    [region, updateMessage, consumeStream, onUnknownRegion],
  )

  const startNewConversation = useCallback(() => {
    setConversationId(undefined)
    setMessages([])
    setError(null)
    setStreamStatus(INITIAL_STREAM_STATUS)
  }, [])

  const clearRegionChangeRequired = useCallback(
    () => setStreamStatus((prev) => ({ ...prev, regionChangeRequired: false })),
    [],
  )

  const loadConversation = useCallback(async (id: string) => {
    const loaded = await getConversationMessages(id)
    setConversationId(id)
    setMessages(loaded.map((m) => ({ id: crypto.randomUUID(), role: m.role, content: m.content })))
    setError(null)
  }, [])

  const clearError = useCallback(() => setError(null), [])

  return {
    conversationId,
    messages,
    isStreaming,
    streamStatus,
    clearRegionChangeRequired,
    error,
    clearError,
    sendMessage,
    respondToConfirmation,
    startNewConversation,
    loadConversation,
  }
}
