# Dano

Dano is an enterprise assistant that turns user requests into controlled actions in connected business systems.

## Language

**curl Tool**:
A native network capability for the Dano Agent, which does not expose a shell. It forwards curl arguments and returns the curl process result.
_Avoid_: REST runtime, HTTP client, policy engine

**Dano Bridge**:
The internal HTTP/SSE and RPC subsystem inside the Dano server that connects browser clients to runtime session capabilities. It is a source-module boundary, not an independent workspace package or separate service.
_Avoid_: bridge workspace package, separate bridge service
