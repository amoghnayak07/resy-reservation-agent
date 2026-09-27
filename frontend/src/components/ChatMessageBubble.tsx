import Box from '@mui/material/Box'
import Paper from '@mui/material/Paper'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import ReactMarkdown from 'react-markdown'
import type { ConfirmationCardState } from '../booking'
import type { ChatMessage } from '../hooks/useChat'
import { ConfirmationCard } from './ConfirmationCard'

function UsageBadge({ usage }: { usage: NonNullable<ChatMessage['usage']> }) {
  return (
    <Typography variant="caption" color="text.secondary">
      {usage.input_tokens} in ({usage.cached_tokens} cached) · {usage.output_tokens} out · $
      {usage.cost_usd.toFixed(4)} · {usage.latency_ms}ms
    </Typography>
  )
}

interface Props {
  message: ChatMessage
  onConfirm?: (card: ConfirmationCardState, passcode: string) => void
  onDecline?: (card: ConfirmationCardState) => void
}

export function ChatMessageBubble({ message, onConfirm, onDecline }: Props) {
  const isHuman = message.role === 'human'
  const { confirmation } = message
  // A turn that only produced a confirmation card has no text; skip the empty bubble.
  const showText = message.content !== '' || !confirmation
  return (
    <Box sx={{ display: 'flex', justifyContent: isHuman ? 'flex-end' : 'flex-start', mb: 1.5 }}>
      <Stack
        sx={{ maxWidth: '80%', alignItems: isHuman ? 'flex-end' : 'flex-start' }}
        spacing={0.5}
      >
        {showText && (
          <Paper
            variant="outlined"
            sx={{
              px: 2,
              py: 1,
              bgcolor: isHuman ? 'primary.main' : 'background.paper',
              color: isHuman ? 'primary.contrastText' : 'text.primary',
            }}
          >
            {isHuman ? (
              <Typography sx={{ whiteSpace: 'pre-wrap' }}>{message.content}</Typography>
            ) : (
              <ReactMarkdown>{message.content}</ReactMarkdown>
            )}
          </Paper>
        )}
        {confirmation && (
          <ConfirmationCard
            card={confirmation}
            onConfirm={(passcode) => onConfirm?.(confirmation, passcode)}
            onDecline={() => onDecline?.(confirmation)}
          />
        )}
        {message.usage && <UsageBadge usage={message.usage} />}
      </Stack>
    </Box>
  )
}
