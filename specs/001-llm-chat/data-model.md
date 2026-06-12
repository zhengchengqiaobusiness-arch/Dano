# Data Model: P0 LLM Chat

## Conversation

Represents one browser-visible chat thread.

**Fields**:

- `id`: Stable conversation identifier.
- `createdAt`: Time conversation was created.
- `updatedAt`: Time conversation last changed.
- `status`: `active`, `answering`, `failed`, or `closed`.
- `messages`: Ordered list of Message entities.

**Validation rules**:

- `id` must be unique within the running server.
- Messages must be displayed by chronological append order.
- P0 does not require persistence after process restart or browser refresh.

## Message

Represents one user input, assistant answer, processing marker, or failure.

**Fields**:

- `id`: Stable message identifier.
- `conversationId`: Parent Conversation id.
- `role`: `user`, `assistant`, or `system`.
- `content`: Text visible to the user.
- `contentBlocks`: Optional ordered text/tool block list when the runtime emits tool calls and results.
- `status`: `pending`, `streaming`, `completed`, or `failed`.
- `createdAt`: Time message was accepted.
- `completedAt`: Time message completed, when applicable.
- `errorCode`: Optional failure category.
- `errorMessage`: Optional user-facing failure text.

**Validation rules**:

- User message content must not be empty after trimming whitespace.
- Assistant messages may stream through deltas but must end as either `completed` or `failed`.
- Failed messages must remain visible and retryable.

## Send Message Request

Represents the browser command to send user text.

**Fields**:

- `conversationId`: Existing Conversation id.
- `clientMessageId`: Browser-generated id used to prevent confusing duplicate sends.
- `text`: User-entered message text.

**Validation rules**:

- `conversationId` must reference an active Conversation.
- `text.trim()` must be non-empty.
- Duplicate `clientMessageId` values for the same conversation must not create duplicate assistant answers.

## SSE Event

Represents a server-to-browser event sent over EventSource.

**Fields**:

- `id`: Monotonic stream event id.
- `event`: Event name.
- `conversationId`: Conversation affected by the event.
- `messageId`: Message affected by the event, when applicable.
- `data`: JSON payload for the event.

**Event names**:

- `conversation.ready`: Conversation created and stream can receive events.
- `message.accepted`: User message accepted by the server.
- `assistant.started`: Assistant answer started.
- `assistant.delta`: Assistant answer text delta.
- `assistant.blocks`: Assistant answer content blocks, including tool-call and tool-result blocks.
- `assistant.completed`: Assistant answer finished.
- `message.failed`: User message or assistant answer failed.
- `heartbeat`: Keepalive event for proxy/browser liveness.

**Validation rules**:

- SSE events for a conversation must preserve append order.
- `message.failed` must include a user-facing `errorMessage`.
- Heartbeats must not mutate conversation state.

## Failure State

Represents a visible recoverable error for one message attempt.

**Fields**:

- `messageId`: Failed Message id.
- `code`: `EMPTY_MESSAGE`, `LLM_UNAVAILABLE`, `LLM_TIMEOUT`, `INVALID_RESPONSE`, `CONNECTION_INTERRUPTED`, or `CONVERSATION_NOT_FOUND`.
- `message`: User-facing failure text.
- `retryable`: Whether the same user message can be retried.

**State transitions**:

```text
pending -> streaming -> completed
pending -> failed
streaming -> failed
failed -> pending (retry)
```
