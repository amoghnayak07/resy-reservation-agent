import { useCallback, useState } from 'react'
import { buildChatRequest } from '../api/chatRequest'
import { ApiError, getConversationMessages, postChatMessage } from '../api/client'
import { parseSSEStream } from '../api/sse'
import type { UsageEvent, UserLocation } from '../api/types'
import { applyStreamEvent, INITIAL_STREAM_STATUS } from './streamStatus'
import type { StreamStatus } from './streamStatus'

export interface ChatMessage {
  id: string
  role: 'human' | 'ai'
  content: string
  usage?: UsageEvent
}

export interface ChatErrorState {
  code: string
  message: string
  retryAfterSeconds?: number
}

export function useChat(
  timezone: string,
  onTurnComplete?: () => void,
  getLocation?: () => Promise<UserLocation | undefined>,
) {
  const [conversationId, setConversationId] = useState<string | undefined>(undefined)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [error, setError] = useState<ChatErrorState | null>(null)
  const [streamStatus, setStreamStatus] = useState<StreamStatus>(INITIAL_STREAM_STATUS)

  const sendMessage = useCallback(
    async (text: string) => {
      setError(null)
      setStreamStatus(INITIAL_STREAM_STATUS)
      const humanMessage: ChatMessage = { id: crypto.randomUUID(), role: 'human', content: text }
      const assistantId = crypto.randomUUID()
      const assistantMessage: ChatMessage = { id: assistantId, role: 'ai', content: '' }
      setMessages((prev) => [...prev, humanMessage, assistantMessage])
      setIsStreaming(true)

      let response
      try {
        const location = await getLocation?.()
        response = await postChatMessage(buildChatRequest(text, timezone, conversationId, location))
      } catch (err) {
        setMessages((prev) => prev.filter((m) => m.id !== assistantId))
        setIsStreaming(false)
        if (err instanceof ApiError) {
          setError({
            code: err.code,
            message: err.message,
            retryAfterSeconds: err.retryAfterSeconds,
          })
        } else {
          setError({ code: 'network_error', message: 'Could not reach the server.' })
        }
        onTurnComplete?.()
        return
      }

      if (!response.body) {
        setIsStreaming(false)
        onTurnComplete?.()
        return
      }

      for await (const event of parseSSEStream(response.body)) {
        setStreamStatus((prev) => applyStreamEvent(prev, event))
        switch (event.event) {
          case 'meta':
            setConversationId(event.data.conversation_id)
            break
          case 'token':
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, content: m.content + event.data.text } : m,
              ),
            )
            break
          case 'usage':
            setMessages((prev) =>
              prev.map((m) => (m.id === assistantId ? { ...m, usage: event.data } : m)),
            )
            break
          case 'error':
            setError({ code: event.data.code, message: event.data.message })
            break
          case 'done':
            setIsStreaming(false)
            break
        }
      }
      setIsStreaming(false)
      onTurnComplete?.()
    },
    [conversationId, timezone, onTurnComplete, getLocation],
  )

  const startNewConversation = useCallback(() => {
    setConversationId(undefined)
    setMessages([])
    setError(null)
    setStreamStatus(INITIAL_STREAM_STATUS)
  }, [])

  const clearLocationRequired = useCallback(
    () => setStreamStatus((prev) => ({ ...prev, locationRequired: false })),
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
    clearLocationRequired,
    error,
    clearError,
    sendMessage,
    startNewConversation,
    loadConversation,
  }
}
