import { useState } from 'react'
import type { KeyboardEvent } from 'react'
import Box from '@mui/material/Box'
import IconButton from '@mui/material/IconButton'
import TextField from '@mui/material/TextField'
import Typography from '@mui/material/Typography'
import SendIcon from '@mui/icons-material/Send'

// Mirrors the backend's MAX_MESSAGE_CHARS default. The frontend has no way to
// read the server's setting (its only env var is VITE_API_BASE_URL), so this
// is a soft client-side counter -- the server's 422 is the real limit.
const MAX_MESSAGE_CHARS = 1000

interface ChatInputProps {
  disabled: boolean
  onSend: (text: string) => void
}

export function ChatInput({ disabled, onSend }: ChatInputProps) {
  const [value, setValue] = useState('')

  const trimmed = value.trim()
  const canSend = !disabled && trimmed.length > 0 && value.length <= MAX_MESSAGE_CHARS

  const send = () => {
    if (!canSend) return
    onSend(trimmed)
    setValue('')
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      send()
    }
  }

  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-end', gap: 1, p: 2 }}>
      <TextField
        fullWidth
        multiline
        maxRows={6}
        placeholder="Ask about a reservation…"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        helperText={
          <Typography
            component="span"
            variant="caption"
            color={value.length > MAX_MESSAGE_CHARS ? 'error' : 'text.secondary'}
          >
            {value.length}/{MAX_MESSAGE_CHARS}
          </Typography>
        }
      />
      <IconButton color="primary" onClick={send} disabled={!canSend} aria-label="Send message">
        <SendIcon />
      </IconButton>
    </Box>
  )
}
