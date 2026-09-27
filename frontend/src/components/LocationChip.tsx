import Box from '@mui/material/Box'
import Chip from '@mui/material/Chip'
import Typography from '@mui/material/Typography'
import LocationOffIcon from '@mui/icons-material/LocationOff'
import LocationOnIcon from '@mui/icons-material/LocationOn'
import MyLocationIcon from '@mui/icons-material/MyLocation'
import type { LocationStatus } from '../location'

interface LocationChipProps {
  status: LocationStatus
  highlighted: boolean // a search needed the location (location_required event)
  onEnable: () => void
  onDisable: () => void
}

const LABELS: Record<LocationStatus, string> = {
  off: 'Use my location',
  requesting: 'Locating…',
  on: 'Using your location',
  denied: 'Location blocked',
  unavailable: 'Location unavailable, tap to retry',
}

const HELP: Partial<Record<LocationStatus, string>> = {
  denied:
    "Location is blocked for this site. Allow it in your browser's site settings, then tap again.",
}

export function LocationChip({ status, highlighted, onEnable, onDisable }: LocationChipProps) {
  const isOn = status === 'on'
  const icon =
    status === 'on' ? (
      <LocationOnIcon />
    ) : status === 'denied' ? (
      <LocationOffIcon />
    ) : (
      <MyLocationIcon />
    )
  const attention = highlighted && !isOn

  return (
    <Box sx={{ px: 2, pt: 1, display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
      <Chip
        icon={icon}
        label={LABELS[status]}
        onClick={isOn ? onDisable : onEnable}
        disabled={status === 'requesting'}
        color={isOn ? 'primary' : attention ? 'warning' : 'default'}
        variant={isOn || attention ? 'filled' : 'outlined'}
        aria-label={isOn ? 'Stop using my location' : 'Use my location'}
      />
      {HELP[status] && (
        <Typography variant="caption" color="text.secondary">
          {HELP[status]}
        </Typography>
      )}
      {attention && status !== 'denied' && (
        <Typography variant="caption" color="warning.main">
          Searches are local, so share your location to search.
        </Typography>
      )}
    </Box>
  )
}
