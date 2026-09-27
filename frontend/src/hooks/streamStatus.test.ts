import { describe, expect, it } from 'vitest'
import type { ChatEvent } from '../api/types'
import { activityLabel, applyStreamEvent, INITIAL_STREAM_STATUS } from './streamStatus'

function run(events: ChatEvent[]) {
  return events.reduce(applyStreamEvent, INITIAL_STREAM_STATUS)
}

describe('stream status', () => {
  it('shows the search indicator on tool_start and clears it on tool_end', () => {
    const started = run([
      { event: 'tool_start', data: { name: 'search_availability', call_id: 'c1' } },
    ])
    expect(activityLabel(started)).toBe('Searching Resy…')

    const ended = applyStreamEvent(started, {
      event: 'tool_end',
      data: { name: 'search_availability', call_id: 'c1', ok: true, duration_ms: 420 },
    })
    expect(ended.activeTools).toEqual({})
    expect(activityLabel(ended)).toBe('Working…')
  })

  it('flags location_required for the chip highlight', () => {
    const status = run([
      { event: 'tool_start', data: { name: 'search_availability', call_id: 'c1' } },
      { event: 'location_required', data: {} },
      {
        event: 'tool_end',
        data: { name: 'search_availability', call_id: 'c1', ok: false, duration_ms: 3 },
      },
    ])
    expect(status.locationRequired).toBe(true)
    expect(status.activeTools).toEqual({})
  })

  it('clears running tools when the stream ends or errors', () => {
    const running = run([
      { event: 'tool_start', data: { name: 'search_availability', call_id: 'c1' } },
    ])
    expect(applyStreamEvent(running, { event: 'done', data: {} }).activeTools).toEqual({})
    expect(
      applyStreamEvent(running, { event: 'error', data: { code: 'x', message: 'y' } }).activeTools,
    ).toEqual({})
  })

  it('uses a generic label for unknown tools', () => {
    const status = run([{ event: 'tool_start', data: { name: 'other_tool', call_id: 'c9' } }])
    expect(activityLabel(status)).toBe('Working…')
  })
})
