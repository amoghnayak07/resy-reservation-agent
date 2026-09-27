import type { ChatEvent } from './types'

function parseBlock(block: string): ChatEvent | null {
  let eventName: string | null = null
  let data: string | null = null

  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) {
      eventName = line.slice('event:'.length).trim()
    } else if (line.startsWith('data:')) {
      data = line.slice('data:'.length).trim()
    }
  }

  if (!eventName || data === null) {
    return null
  }

  try {
    return { event: eventName, data: JSON.parse(data) } as ChatEvent
  } catch {
    return null
  }
}

export async function* parseSSEStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<ChatEvent> {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      let separatorIndex: number
      while ((separatorIndex = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, separatorIndex)
        buffer = buffer.slice(separatorIndex + 2)

        if (!block.trim() || block.startsWith(':')) {
          continue
        }

        const event = parseBlock(block)
        if (event) {
          yield event
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}
