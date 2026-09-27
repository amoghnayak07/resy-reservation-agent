import { describe, expect, it } from 'vitest'
import { parseSSEStream } from './sse'

function streamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  let index = 0
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (index < chunks.length) {
        controller.enqueue(encoder.encode(chunks[index]))
        index += 1
      } else {
        controller.close()
      }
    },
  })
}

async function collect(stream: ReadableStream<Uint8Array>) {
  const events = []
  for await (const event of parseSSEStream(stream)) {
    events.push(event)
  }
  return events
}

describe('parseSSEStream', () => {
  it('parses a single event in one chunk', async () => {
    const stream = streamFromChunks(['event: meta\ndata: {"conversation_id":"abc"}\n\n'])
    const events = await collect(stream)
    expect(events).toEqual([{ event: 'meta', data: { conversation_id: 'abc' } }])
  })

  it('parses an event split across multiple chunks', async () => {
    const stream = streamFromChunks(['event: tok', 'en\ndata: {"te', 'xt":"hi"}\n\n'])
    const events = await collect(stream)
    expect(events).toEqual([{ event: 'token', data: { text: 'hi' } }])
  })

  it('parses multiple events in a single chunk', async () => {
    const stream = streamFromChunks([
      'event: token\ndata: {"text":"a"}\n\nevent: token\ndata: {"text":"b"}\n\n',
    ])
    const events = await collect(stream)
    expect(events).toEqual([
      { event: 'token', data: { text: 'a' } },
      { event: 'token', data: { text: 'b' } },
    ])
  })

  it('ignores ping comments', async () => {
    const stream = streamFromChunks([
      'event: token\ndata: {"text":"a"}\n\n: ping\n\nevent: done\ndata: {}\n\n',
    ])
    const events = await collect(stream)
    expect(events).toEqual([
      { event: 'token', data: { text: 'a' } },
      { event: 'done', data: {} },
    ])
  })

  it('skips malformed blocks (missing data, invalid JSON)', async () => {
    const stream = streamFromChunks([
      'event: token\n\nevent: token\ndata: not-json\n\nevent: done\ndata: {}\n\n',
    ])
    const events = await collect(stream)
    expect(events).toEqual([{ event: 'done', data: {} }])
  })
})
