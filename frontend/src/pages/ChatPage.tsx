import { useCallback, useEffect, useRef, useState } from 'react'
import MenuIcon from '@mui/icons-material/Menu'
import Alert from '@mui/material/Alert'
import AppBar from '@mui/material/AppBar'
import Box from '@mui/material/Box'
import IconButton from '@mui/material/IconButton'
import Snackbar from '@mui/material/Snackbar'
import Toolbar from '@mui/material/Toolbar'
import Typography from '@mui/material/Typography'
import { ChatInput } from '../components/ChatInput'
import { ChatMessageBubble } from '../components/ChatMessageBubble'
import { ConversationDrawer } from '../components/ConversationDrawer'
import { WakeUpBanner } from '../components/WakeUpBanner'
import { useChat } from '../hooks/useChat'
import { useHealthCheck } from '../hooks/useHealthCheck'

const TIMEZONE = Intl.DateTimeFormat().resolvedOptions().timeZone

export function ChatPage() {
  const { state: healthState, retry: retryHealth } = useHealthCheck()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)
  const scrollAnchorRef = useRef<HTMLDivElement>(null)

  const handleTurnComplete = useCallback(() => setRefreshKey((key) => key + 1), [])

  const {
    conversationId,
    messages,
    isStreaming,
    error,
    clearError,
    sendMessage,
    startNewConversation,
    loadConversation,
  } = useChat(TIMEZONE, handleTurnComplete)

  useEffect(() => {
    scrollAnchorRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const isHealthy = healthState === 'healthy'

  const handleSelect = (id: string) => {
    void loadConversation(id)
    setDrawerOpen(false)
  }

  const handleNewChat = () => {
    startNewConversation()
    setDrawerOpen(false)
  }

  return (
    <Box sx={{ display: 'flex', height: '100%' }}>
      <ConversationDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        activeConversationId={conversationId}
        onSelect={handleSelect}
        onNewChat={handleNewChat}
        refreshKey={refreshKey}
      />
      <Box sx={{ display: 'flex', flexDirection: 'column', flexGrow: 1, height: '100%' }}>
        <AppBar position="static" color="default" sx={{ display: { sm: 'none' } }}>
          <Toolbar>
            <IconButton
              edge="start"
              onClick={() => setDrawerOpen(true)}
              aria-label="Open conversations"
            >
              <MenuIcon />
            </IconButton>
            <Typography variant="h6" sx={{ ml: 1 }}>
              Resy Reservation Agent
            </Typography>
          </Toolbar>
        </AppBar>

        <WakeUpBanner state={healthState} onRetry={retryHealth} />

        <Box sx={{ flexGrow: 1, overflowY: 'auto', p: 2 }}>
          {messages.map((message) => (
            <ChatMessageBubble key={message.id} message={message} />
          ))}
          {isStreaming && (
            <Typography variant="body2" color="text.secondary">
              Working…
            </Typography>
          )}
          <div ref={scrollAnchorRef} />
        </Box>

        <ChatInput disabled={!isHealthy || isStreaming} onSend={(text) => void sendMessage(text)} />
      </Box>

      <Snackbar open={error !== null} autoHideDuration={6000} onClose={clearError}>
        <Alert severity="error" onClose={clearError} sx={{ width: '100%' }}>
          {error?.code === 'rate_limited' && error.retryAfterSeconds
            ? `Too many messages. Try again in ${error.retryAfterSeconds}s.`
            : error?.message}
        </Alert>
      </Snackbar>
    </Box>
  )
}
