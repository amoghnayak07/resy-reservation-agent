import Alert from '@mui/material/Alert'
import Button from '@mui/material/Button'
import CircularProgress from '@mui/material/CircularProgress'
import type { HealthState } from '../hooks/useHealthCheck'

interface WakeUpBannerProps {
  state: HealthState
  onRetry: () => void
}

export function WakeUpBanner({ state, onRetry }: WakeUpBannerProps) {
  if (state === 'healthy') return null

  if (state === 'timed_out') {
    return (
      <Alert
        severity="warning"
        action={
          <Button color="inherit" size="small" onClick={onRetry}>
            Retry
          </Button>
        }
      >
        The server is taking longer than expected to wake up.
      </Alert>
    )
  }

  return (
    <Alert severity="info" icon={<CircularProgress size={20} />}>
      Waking up the server (free tier), this can take about a minute.
    </Alert>
  )
}
