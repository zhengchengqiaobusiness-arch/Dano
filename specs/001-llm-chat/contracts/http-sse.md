# Contract: HTTP Commands + EventSource Stream

## Overview

Browser commands use HTTP requests. Server-to-browser updates use EventSource/SSE. WebSocket is not part of the P0 browser contract.

All endpoints are same-origin behind nginx.

## `GET /api/health`

Returns service readiness for container and proxy checks.

**Response 200**:

```json
{
  "status": "ok"
}
```

## `POST /api/conversations`

Creates a browser chat conversation before the EventSource stream starts.

**Request**:

```json
{}
```

**Response 201**:

```json
{
  "conversationId": "conv_123",
  "eventsUrl": "/api/conversations/conv_123/events"
}
```

## `GET /api/conversations/{conversationId}/events`

Opens the EventSource stream for one conversation.

**Response headers**:

```text
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```

**Events**:

```text
id: 1
event: conversation.ready
data: {"conversationId":"conv_123"}

id: 2
event: message.accepted
data: {"conversationId":"conv_123","messageId":"msg_1","role":"user","content":"Hello"}

id: 3
event: assistant.started
data: {"conversationId":"conv_123","messageId":"msg_2"}

id: 4
event: assistant.delta
data: {"conversationId":"conv_123","messageId":"msg_2","delta":"Hi"}

id: 5
event: assistant.completed
data: {"conversationId":"conv_123","messageId":"msg_2","content":"Hi there."}

event: heartbeat
data: {}
```

**Failure event**:

```text
event: message.failed
data: {"conversationId":"conv_123","messageId":"msg_2","code":"LLM_TIMEOUT","errorMessage":"The assistant did not answer in time.","retryable":true}
```

## `POST /api/conversations/{conversationId}/messages`

Sends a user message to the server-side LLM runtime.

**Request**:

```json
{
  "clientMessageId": "client_msg_123",
  "text": "Hello"
}
```

**Response 202**:

```json
{
  "conversationId": "conv_123",
  "messageId": "msg_1",
  "status": "accepted"
}
```

**Response 400**:

```json
{
  "code": "EMPTY_MESSAGE",
  "errorMessage": "Enter a message before sending."
}
```

**Response 404**:

```json
{
  "code": "CONVERSATION_NOT_FOUND",
  "errorMessage": "Conversation was not found."
}
```

## `POST /api/conversations/{conversationId}/messages/{messageId}/retry`

Retries a failed user message without requiring the user to retype it.

**Request**:

```json
{}
```

**Response 202**:

```json
{
  "conversationId": "conv_123",
  "messageId": "msg_3",
  "status": "accepted"
}
```

## nginx Proxy Requirements

The nginx route for EventSource must disable response buffering and keep the connection open:

```text
proxy_buffering off;
proxy_cache off;
proxy_read_timeout 3600s;
```

All browser routes and `/api/*` routes proxy to the app service on the Docker network. Browser users never connect directly to the app container.
