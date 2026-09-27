from uuid import UUID

from fastapi import APIRouter, Depends
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.api.deps import get_conversation_repository, get_graph, get_session_id
from app.api.message_text import content_to_text
from app.db.repository import ConversationRepository
from app.schemas.conversations import ConversationOut, MessageOut

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _to_message_out(message: BaseMessage) -> MessageOut | None:
    """Human and AI text messages only -- tool messages and empty tool-call-only
    AI messages are internal and hidden from the client."""
    if isinstance(message, HumanMessage):
        return MessageOut(role="human", content=content_to_text(message.content))
    if isinstance(message, AIMessage):
        text = content_to_text(message.content)
        if not text:
            return None
        return MessageOut(role="ai", content=text)
    return None


@router.post("", response_model=ConversationOut, status_code=201)
async def create_conversation(
    session_id: str = Depends(get_session_id),
    repo: ConversationRepository = Depends(get_conversation_repository),
) -> ConversationOut:
    conversation = await repo.create(session_id)
    return ConversationOut.model_validate(conversation)


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    session_id: str = Depends(get_session_id),
    repo: ConversationRepository = Depends(get_conversation_repository),
) -> list[ConversationOut]:
    conversations = await repo.list_for_session(session_id)
    return [ConversationOut.model_validate(c) for c in conversations]


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
async def get_conversation_messages(
    conversation_id: UUID,
    session_id: str = Depends(get_session_id),
    repo: ConversationRepository = Depends(get_conversation_repository),
    graph: CompiledStateGraph = Depends(get_graph),
) -> list[MessageOut]:
    conversation = await repo.get_owned(conversation_id, session_id)
    config: RunnableConfig = {"configurable": {"thread_id": str(conversation.id)}}
    snapshot = await graph.aget_state(config)
    messages = snapshot.values.get("messages", []) if snapshot else []
    return [out for m in messages if (out := _to_message_out(m)) is not None]
