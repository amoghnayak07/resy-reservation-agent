import { useCallback, useEffect, useEffectEvent, useState } from 'react'
import { getHealth } from '../api/client'

const POLL_INTERVAL_MS = 3000
const TIMEOUT_MS = 90000
const REQUEST_TIMEOUT_MS = 5000

export type HealthState = 'checking' | 'healthy' | 'timed_out'

export function useHealthCheck() {
  const [state, setState] = useState<HealthState>('checking')
  const [deadline, setDeadline] = useState(() => Date.now() + TIMEOUT_MS)

  const check = useEffectEvent(async () => {
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
    try {
      await getHealth(controller.signal)
      setState('healthy')
    } catch {
      if (Date.now() > deadline) {
        setState('timed_out')
      }
    } finally {
      clearTimeout(timeoutId)
    }
  })

  useEffect(() => {
    if (state !== 'checking') return
    // `check` is a stable useEffectEvent; run it via a callback boundary
    // (rather than calling it directly in the effect body) for both the
    // immediate check and the recurring poll.
    const immediate = setTimeout(() => void check(), 0)
    const interval = setInterval(() => void check(), POLL_INTERVAL_MS)
    return () => {
      clearTimeout(immediate)
      clearInterval(interval)
    }
  }, [state])

  const retry = useCallback(() => {
    setDeadline(Date.now() + TIMEOUT_MS)
    setState('checking')
  }, [])

  return { state, retry }
}
