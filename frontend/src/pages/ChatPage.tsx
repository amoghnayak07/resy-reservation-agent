import { useEffect, useState } from 'react'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import CircularProgress from '@mui/material/CircularProgress'
import Alert from '@mui/material/Alert'
import { getHealth } from '../api/client'
import type { HealthResponse } from '../api/types'

export function ChatPage() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'Unknown error')
      })
  }, [])

  return (
    <Box sx={{ p: 4 }}>
      <Typography variant="h4" gutterBottom>
        Resy Reservation Agent
      </Typography>
      <Typography variant="body1" gutterBottom>
        Chat UI coming in a later stage. Backend health check:
      </Typography>
      {error && <Alert severity="error">{error}</Alert>}
      {!error && !health && <CircularProgress size={24} />}
      {health && (
        <Alert severity="success">
          status: {health.status} · env: {health.env} · version: {health.version}
        </Alert>
      )}
    </Box>
  )
}
