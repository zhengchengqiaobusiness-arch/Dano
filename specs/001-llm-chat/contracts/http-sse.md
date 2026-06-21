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

## `POST /api/clients`

Creates a logical browser client before the EventSource stream starts.

**Request**:

```json
{}
```

**Response 201**:

```json
{
  "client": {
    "id": "client_123",
    "seq": 1,
    "connectedAt": "2026-06-17T03:25:59.017Z"
  },
  "eventsUrl": "/api/clients/client_123/events",
  "messagesUrl": "/api/clients/client_123/messages",
  "defaultWorkspacePath": "/tmp/dano"
}
```

## `GET /api/clients/{clientId}/events`

Opens the EventSource stream for one logical browser client.

**Response headers**:

```text
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```

**Messages**:

```text
data: {"type":"response","payload":{"id":"cmd-1","type":"response","command":"get_state","success":true,"data":{}}}

data: {"type":"event","payload":{"type":"agent_start"}}

: heartbeat
```

**Failure response**:

```text
data: {"type":"response","payload":{"id":"cmd-1","type":"response","command":"prompt","success":false,"error":"No API key found for the selected model."}}
```

## `POST /api/clients/{clientId}/messages`

Sends a command envelope to the server-side LLM runtime.

**Request**:

```json
{
  "type": "command",
  "payload": {
    "id": "cmd-1",
    "type": "get_state"
  }
}
```

**Response 202**:

```json
{
  "status": "accepted"
}
```

**Response 400**:

```json
{
  "error": "Request body must be a client message"
}
```

**Response 404**:

```json
{
  "error": "Client was not found"
}
```

## `POST /api/clients/{clientId}/disconnect`

Disconnects a logical browser client.

**Request**:

```json
{}
```

**Response 202**:

```json
{
  "status": "disconnected"
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
