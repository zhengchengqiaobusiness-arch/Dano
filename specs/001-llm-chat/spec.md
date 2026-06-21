# Feature Specification: P0 LLM Chat

**Feature Branch**: `001-llm-chat`

**Created**: 2026-06-11

**Status**: Draft

**Input**: User description: "check the enterprise-business-operation-agent-prd.md make a spec of p0 - p0 需求保证在客户端浏览器能正常和服务端运行的 llm 通信和对话就行了。具体业务流程相关的先不关注"

## Clarifications

### Session 2026-06-11

- Q: LLM API key 如何配置, 在哪里配置? → A: Server-side environment variables only; local `.env`, Docker env/secrets, app reads from server process env.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Send First Message (Priority: P1)

As a browser user, I can open the chat experience, type a message, send it, and receive a visible answer from the server-side LLM so I know the basic conversation loop works.

**Why this priority**: This is the minimum usable P0 slice. Without a successful browser-to-server-to-LLM reply, no later business-agent capability can be validated.

**Independent Test**: Can be fully tested by opening the browser chat, sending one normal message, and confirming the user message plus LLM answer appear in order.

**Acceptance Scenarios**:

1. **Given** the browser chat is available, **When** the user sends "你好，介绍一下你能做什么", **Then** the conversation shows the user message and one LLM answer in the same chat thread.
2. **Given** the user has typed a non-empty message, **When** the user sends it, **Then** the interface shows that the request is being processed until a final answer or clear failure appears.

---

### User Story 2 - Continue Conversation (Priority: P2)

As a browser user, I can send follow-up messages in the same chat thread so the interaction feels like a real conversation rather than isolated question answering.

**Why this priority**: P0 requires dialogue, not only one-shot text completion. Multi-turn continuity verifies that the chat experience can support later agent behavior.

**Independent Test**: Can be fully tested by sending an initial message, then a follow-up that refers to the earlier message, and confirming the answer is shown in the same ordered thread.

**Acceptance Scenarios**:

1. **Given** a chat already contains a user message and an LLM answer, **When** the user sends a follow-up question, **Then** the new user message and answer appear after the earlier messages without reordering or losing the earlier content.
2. **Given** the user sends several messages in one session, **When** all answers complete, **Then** each answer remains associated with the message that triggered it.

---

### User Story 3 - Recover From Chat Problems (Priority: P3)

As a browser user, I receive clear feedback when a message cannot be answered, and I can retry without losing the conversation that already exists.

**Why this priority**: P0 must prove the communication path fails loud. Silent failures would make later business scenarios unsafe to build on.

**Independent Test**: Can be fully tested by causing an unavailable or delayed LLM response and confirming the browser shows a clear error state with retry path while preserving existing messages.

**Acceptance Scenarios**:

1. **Given** the server-side LLM cannot provide an answer, **When** the user sends a message, **Then** the conversation shows a clear failure state and no fake successful answer.
2. **Given** a failed message is visible, **When** the user retries, **Then** the system attempts to answer again while preserving the previous chat history visible to the user.

### Edge Cases

- Empty or whitespace-only messages are not sent and receive immediate user-facing validation.
- Very long user messages are either accepted with a clear processing state or rejected with a clear length-related message before sending.
- Duplicate send attempts during processing do not create confusing duplicate assistant answers.
- Service unavailability, delayed answers, or interrupted communication show a clear failure state instead of hanging forever.
- Business-process requests such as leave application, approval, reimbursement, or system operation are treated as ordinary chat content only; P0 does not execute business actions.
- Sensitive service credentials or model access secrets are never visible to browser users.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a browser-accessible chat entry where an allowed user can enter and send a text message.
- **FR-002**: System MUST deliver each sent user message to the server-side LLM capability and display the returned answer in the same chat thread.
- **FR-003**: System MUST display user messages and LLM answers in chronological order within the active conversation.
- **FR-004**: System MUST show a processing state after a message is sent and before the final answer or failure state is available.
- **FR-005**: System MUST prevent empty or whitespace-only messages from being submitted.
- **FR-006**: System MUST keep existing visible conversation messages available after a later message fails.
- **FR-007**: System MUST let the user retry a failed message without retyping the whole conversation.
- **FR-008**: System MUST show clear user-facing feedback when the LLM answer cannot be produced due to service unavailability, delayed response, invalid response, or interrupted communication.
- **FR-009**: System MUST avoid exposing sensitive service credentials or model access secrets to browser users.
- **FR-010**: System MUST bound P0 behavior to chat and conversation only; it MUST NOT submit forms, operate enterprise systems, trigger approvals, create business records, or run business workflow actions.
- **FR-011**: System MUST make unsupported business-action requests visibly non-executing, either by answering conversationally or by stating that business execution is outside P0 scope.
- **FR-012**: System MUST support at least five verification cases before P0 is considered ready: normal first message, multi-turn follow-up, empty input, failed LLM response, and business-action request with no execution.
- **FR-013**: System MUST load LLM access credentials only from server-side environment configuration, including local `.env` and Docker-provided environment or secret values; it MUST NOT accept, persist, or display LLM API keys through the browser UI.
- **FR-014**: System MUST render assistant tool-call and tool-result blocks when the server-side LLM runtime emits them, using the same inline collapsed/expandable presentation model as `references/pi-web-main`.

### Key Entities

- **Conversation**: Active chat thread visible in the browser. Contains ordered messages for the current user session.
- **Message**: A single user input, LLM answer, processing state, or failure state in the conversation.
- **LLM Answer**: Text response produced by the server-side LLM capability and shown to the browser user.
- **Failure State**: User-facing status explaining that a message was not answered successfully and can be retried.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In a controlled P0 verification run, at least 95% of normal text messages produce either a visible LLM answer or a clear failure state within 30 seconds.
- **SC-002**: A first-time tester can send a first message and identify the LLM answer in under 30 seconds after opening the chat.
- **SC-003**: In a five-message conversation test, 100% of visible messages remain in chronological order with no lost user messages.
- **SC-004**: In the required five-case verification set, all cases produce the expected visible outcome without silent failure.
- **SC-005**: Business-action requests in P0 result in zero submitted enterprise forms, approvals, records, or system operations.
- **SC-006**: Browser-visible inspection during verification reveals zero sensitive service credentials or model access secrets.
- **SC-007**: P0 verification demonstrates that changing the server-side LLM credential configuration changes the server's model access behavior without requiring any browser-side secret input.

## Assumptions

- P0 is intentionally narrower than the PRD's original P0 business-agent scope.
- P0 validates browser chat communication with a server-side LLM before business workflow, Skill, connector, or enterprise-system execution is introduced.
- Users are already allowed to access the P0 chat surface through the surrounding product or test environment.
- LLM API keys and model access secrets are configured only on the server side, using local `.env` for development and Docker environment or secret injection for container deployment.
- Conversation persistence across browser refresh, devices, or long-term history is outside this P0 spec unless added later.
- File upload, voice input, enterprise-system actions, and administrative Skill management are outside this P0 spec.
