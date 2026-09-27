import { useState } from 'react'
import Alert from '@mui/material/Alert'
import Autocomplete from '@mui/material/Autocomplete'
import Button from '@mui/material/Button'
import CircularProgress from '@mui/material/CircularProgress'
import Dialog from '@mui/material/Dialog'
import DialogActions from '@mui/material/DialogActions'
import DialogContent from '@mui/material/DialogContent'
import DialogTitle from '@mui/material/DialogTitle'
import Stack from '@mui/material/Stack'
import TextField from '@mui/material/TextField'
import Typography from '@mui/material/Typography'
import type { RegionCity, RegionCountry } from '../api/types'
import { citiesFor, toSelectedRegion } from '../region'
import type { SelectedRegion } from '../region'
import type { RegionsState } from '../hooks/useRegion'

interface Props {
  open: boolean
  // No region yet: the dialog can't be dismissed until one is chosen.
  required: boolean
  countries: RegionCountry[]
  state: RegionsState
  current: SelectedRegion | null
  onSelect: (region: SelectedRegion) => void
  onClose: () => void
  onRetry: () => void
}

// Mounted only while open (see ChatPage), so each opening starts from the current region.
export function RegionDialog(props: Props) {
  return props.open ? <RegionDialogContent {...props} /> : null
}

function RegionDialogContent({
  required,
  countries,
  state,
  current,
  onSelect,
  onClose,
  onRetry,
}: Props) {
  const [country, setCountry] = useState<RegionCountry | null>(
    () => countries.find((c) => c.code === current?.countryCode) ?? null,
  )
  const [city, setCity] = useState<RegionCity | null>(() =>
    country ? (country.cities.find((c) => c.slug === current?.slug) ?? null) : null,
  )
  const cities = citiesFor(countries, country?.code ?? null)

  return (
    <Dialog
      open
      // Backdrop clicks and Escape are ignored until a region exists.
      onClose={() => {
        if (!required) onClose()
      }}
      fullWidth
      maxWidth="xs"
    >
      <DialogTitle>Choose your location</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          The agent searches and books restaurants in this city, in its local time.
        </Typography>
        {state === 'loading' && <CircularProgress size={24} aria-label="Loading cities" />}
        {state === 'error' && (
          <Alert
            severity="error"
            action={
              <Button color="inherit" size="small" onClick={onRetry}>
                Retry
              </Button>
            }
          >
            Couldn't load Resy's cities.
          </Alert>
        )}
        {state === 'ready' && (
          <Stack spacing={2} sx={{ pt: 1 }}>
            <Autocomplete
              options={countries}
              value={country}
              onChange={(_, next) => {
                setCountry(next)
                setCity(null)
              }}
              getOptionLabel={(c) => c.name}
              isOptionEqualToValue={(a, b) => a.code === b.code}
              renderInput={(params) => <TextField {...params} label="Country" autoFocus />}
            />
            <Autocomplete
              options={cities}
              value={city}
              onChange={(_, next) => setCity(next)}
              getOptionLabel={(c) => c.name}
              isOptionEqualToValue={(a, b) => a.slug === b.slug}
              disabled={!country}
              renderInput={(params) => <TextField {...params} label="City or region" />}
            />
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        {!required && <Button onClick={onClose}>Cancel</Button>}
        <Button
          variant="contained"
          disabled={!country || !city}
          onClick={() => country && city && onSelect(toSelectedRegion(country, city))}
        >
          Done
        </Button>
      </DialogActions>
    </Dialog>
  )
}
