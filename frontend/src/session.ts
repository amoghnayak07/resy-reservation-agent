const SESSION_ID_KEY = 'sessionId'

export function getSessionId(): string {
  let sessionId = sessionStorage.getItem(SESSION_ID_KEY)
  if (!sessionId) {
    sessionId = crypto.randomUUID()
    sessionStorage.setItem(SESSION_ID_KEY, sessionId)
  }
  return sessionId
}
