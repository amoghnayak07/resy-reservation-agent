import { describe, expect, it } from 'vitest'
import { buildChatRequest } from './chatRequest'

describe('buildChatRequest', () => {
  it('omits user_location when location is off', () => {
    const body = buildChatRequest('hi', 'America/New_York', undefined, undefined)
    expect(body).toEqual({
      conversation_id: undefined,
      message: 'hi',
      timezone: 'America/New_York',
    })
    expect('user_location' in body).toBe(false)
  })

  it('attaches user_location when location is on', () => {
    const location = { lat: 40.7128, lng: -74.006, accuracy_m: 25 }
    const body = buildChatRequest('hi', 'America/New_York', 'conv-1', location)
    expect(body.user_location).toEqual(location)
    expect(body.conversation_id).toBe('conv-1')
  })
})
