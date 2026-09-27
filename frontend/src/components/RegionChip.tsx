import PlaceIcon from '@mui/icons-material/Place'
import Chip from '@mui/material/Chip'
import type { SelectedRegion } from '../region'

interface Props {
  region: SelectedRegion | null
  // The agent asked the user to change location (region_change_required event).
  highlighted: boolean
  onClick: () => void
}

export function RegionChip({ region, highlighted, onClick }: Props) {
  return (
    <Chip
      icon={<PlaceIcon />}
      label={region ? region.name : 'Choose location'}
      onClick={onClick}
      color={highlighted || !region ? 'primary' : 'default'}
      variant={highlighted ? 'filled' : 'outlined'}
      aria-label={region ? `Location: ${region.name}. Change location` : 'Choose location'}
    />
  )
}
