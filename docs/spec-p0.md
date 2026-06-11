## P0 Requirements

- System MUST provide a runnable browser-based Web Demo with an AI 办事助手 chat window.
- System MUST connect the browser client to the Assistant Backend through EventSource / Server-Sent Events.
- System MUST allow users to send natural-language messages from the browser chat window.
- System MUST forward user messages to a server-side LLM runtime.
- System MUST stream or return model-backed assistant responses to the browser.
- System MUST show connection states including connecting, connected, disconnected, and error.
- System MUST show loading or streaming status while the assistant is processing.
- System MUST provide clear error messages and retry entry points for recoverable model or connection failures.
- System MUST support user-provided model configuration for an OpenAI-compatible provider, including provider type, service address, model, and API key.
- System MUST treat model credentials as sensitive data and MUST NOT expose full credentials in repository files, user-facing errors, chat transcript, SSE events, or logs.
