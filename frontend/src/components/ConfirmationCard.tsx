import { useEffect, useState } from 'react'
import Alert from '@mui/material/Alert'
import Button from '@mui/material/Button'
import Card from '@mui/material/Card'
import CardActions from '@mui/material/CardActions'
import CardContent from '@mui/material/CardContent'
import LinearProgress from '@mui/material/LinearProgress'
import Stack from '@mui/material/Stack'
import TextField from '@mui/material/TextField'
import Typography from '@mui/material/Typography'
import { formatCountdown, secondsLeft } from '../booking'
import type { ConfirmationCardState } from '../booking'

interface Props {
  card: ConfirmationCardState
  onConfirm: (passcode: string) => void
  onDecline: () => void
}

function Row({ label, value }: { label: string; value?: string | number }) {
  if (value === undefined) return null
  return (
    <Typography variant="body2">
      <strong>{label}:</strong> {value}
    </Typography>
  )
}

export function ConfirmationCard({ card, onConfirm, onDecline }: Props) {
  const { summary } = card
  const [passcode, setPasscode] = useState('')
  const [now, setNow] = useState(() => Date.now())

  const isOpen = card.status === 'open'
  useEffect(() => {
    if (!isOpen) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [isOpen])

  const remaining = secondsLeft(summary.expires_at, now)
  const expired = isOpen && remaining === 0
  const actionable = isOpen && !expired

  const submit = () => {
    if (actionable && passcode) onConfirm(passcode)
  }

  return (
    <Card variant="outlined" sx={{ mt: 1, maxWidth: 420 }}>
      {card.status === 'submitting' && <LinearProgress />}
      <CardContent>
        <Typography variant="subtitle1" gutterBottom>
          Confirm reservation
        </Typography>
        <Stack spacing={0.5}>
          <Row label="Restaurant" value={summary.restaurant} />
          <Row label="Address" value={summary.address} />
          <Row label="When" value={`${summary.weekday} ${summary.date}, ${summary.time}`} />
          <Row label="Party" value={summary.party_size} />
          <Row label="Seating" value={summary.seating} />
          <Row label="Cost" value={summary.cost} />
          <Row label="Cancellation" value={summary.cancellation_policy} />
          <Row label="Free cancellation until" value={summary.free_cancellation_until} />
        </Stack>
        {isOpen && (
          <Typography variant="caption" color={expired ? 'error' : 'text.secondary'}>
            {expired ? 'This hold expired.' : `Hold expires in ${formatCountdown(remaining)}`}
          </Typography>
        )}
        {card.error && isOpen && (
          <Alert severity="error" sx={{ mt: 1 }}>
            {card.error}
          </Alert>
        )}
        {card.status === 'confirmed' && (
          <Alert severity="info" sx={{ mt: 1 }}>
            Confirmed. Booking on Resy…
          </Alert>
        )}
        {card.status === 'declined' && (
          <Alert severity="info" sx={{ mt: 1 }}>
            Declined. Nothing was booked.
          </Alert>
        )}
        {card.status === 'closed' && card.note && (
          <Alert severity="warning" sx={{ mt: 1 }}>
            {card.note}
          </Alert>
        )}
      </CardContent>
      {(isOpen || card.status === 'submitting') && (
        <CardActions sx={{ px: 2, pb: 2, flexWrap: 'wrap', gap: 1 }}>
          <TextField
            size="small"
            type="password"
            label="Passcode"
            value={passcode}
            onChange={(e) => setPasscode(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit()
            }}
            disabled={!actionable}
            autoComplete="off"
          />
          <Button variant="contained" onClick={submit} disabled={!actionable || !passcode}>
            Confirm
          </Button>
          <Button onClick={onDecline} disabled={!actionable}>
            Decline
          </Button>
        </CardActions>
      )}
    </Card>
  )
}
