import { useEffect, useState } from 'react'
import AddIcon from '@mui/icons-material/Add'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Divider from '@mui/material/Divider'
import Drawer from '@mui/material/Drawer'
import List from '@mui/material/List'
import ListItemButton from '@mui/material/ListItemButton'
import ListItemText from '@mui/material/ListItemText'
import Toolbar from '@mui/material/Toolbar'
import { listConversations } from '../api/client'
import type { ConversationOut } from '../api/types'

const DRAWER_WIDTH = 280

interface ConversationDrawerProps {
  open: boolean
  onClose: () => void
  activeConversationId: string | undefined
  onSelect: (id: string) => void
  onNewChat: () => void
  refreshKey: number
}

export function ConversationDrawer({
  open,
  onClose,
  activeConversationId,
  onSelect,
  onNewChat,
  refreshKey,
}: ConversationDrawerProps) {
  const [conversations, setConversations] = useState<ConversationOut[]>([])

  useEffect(() => {
    listConversations()
      .then(setConversations)
      .catch(() => setConversations([]))
  }, [refreshKey])

  const content = (
    <Box sx={{ width: DRAWER_WIDTH }} role="presentation">
      <Toolbar sx={{ px: 2 }}>
        <Button fullWidth variant="outlined" startIcon={<AddIcon />} onClick={onNewChat}>
          New chat
        </Button>
      </Toolbar>
      <Divider />
      <List>
        {conversations.map((conversation) => (
          <ListItemButton
            key={conversation.id}
            selected={conversation.id === activeConversationId}
            onClick={() => onSelect(conversation.id)}
          >
            <ListItemText
              primary={conversation.title ?? 'New conversation'}
              slotProps={{ primary: { noWrap: true } }}
            />
          </ListItemButton>
        ))}
      </List>
    </Box>
  )

  return (
    <>
      <Drawer
        variant="permanent"
        sx={{ display: { xs: 'none', sm: 'block' }, width: DRAWER_WIDTH }}
      >
        {content}
      </Drawer>
      <Drawer
        variant="temporary"
        open={open}
        onClose={onClose}
        sx={{ display: { xs: 'block', sm: 'none' } }}
        ModalProps={{ keepMounted: true }}
      >
        {content}
      </Drawer>
    </>
  )
}
