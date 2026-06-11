export type ConnectionStatus = "connecting" | "connected" | "disconnected";

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: "pending" | "streaming" | "completed" | "failed";
  errorMessage?: string;
  retryable?: boolean;
};

export type ChatState = {
  conversationId: string | null;
  eventsUrl: string | null;
  connectionStatus: ConnectionStatus;
  messages: ChatMessage[];
  inputError: string;
  lastError: string;
  sending: boolean;
};

type ServerEvent = {
  conversationId?: string;
  messageId?: string;
  role?: "user" | "assistant";
  content?: string;
  delta?: string;
  errorMessage?: string;
  retryable?: boolean;
};

export function createInitialChatState(): ChatState {
  return {
    conversationId: null,
    eventsUrl: null,
    connectionStatus: "connecting",
    messages: [],
    inputError: "",
    lastError: "",
    sending: false,
  };
}

export function canSend(text: string, state: ChatState): boolean {
  return Boolean(text.trim()) && !state.sending;
}

export function createClientMessageId(): string {
  return `client_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}

export function applyServerEvent(
  state: ChatState,
  eventName: string,
  data: ServerEvent,
): ChatState {
  switch (eventName) {
    case "conversation.ready":
      return {
        ...state,
        conversationId: data.conversationId ?? state.conversationId,
        connectionStatus: "connected",
        lastError: "",
      };

    case "message.accepted":
      if (!data.messageId || data.role !== "user") {
        return state;
      }
      return {
        ...state,
        sending: false,
        inputError: "",
        messages: upsertMessage(state.messages, {
          id: data.messageId,
          role: "user",
          content: data.content ?? "",
          status: "completed",
        }),
      };

    case "assistant.started":
      if (!data.messageId) {
        return state;
      }
      return {
        ...state,
        messages: upsertMessage(state.messages, {
          id: data.messageId,
          role: "assistant",
          content: "",
          status: "streaming",
        }),
      };

    case "assistant.delta":
      if (!data.messageId || !data.delta) {
        return state;
      }
      return {
        ...state,
        messages: state.messages.map(message =>
          message.id === data.messageId
            ? {
                ...message,
                content: `${message.content}${data.delta}`,
                status: "streaming",
              }
            : message,
        ),
      };

    case "assistant.completed":
      if (!data.messageId) {
        return state;
      }
      return {
        ...state,
        sending: false,
        messages: state.messages.map(message =>
          message.id === data.messageId
            ? {
                ...message,
                content: data.content ?? message.content,
                status: "completed",
              }
            : message,
        ),
      };

    case "message.failed":
      if (!data.messageId) {
        return state;
      }
      return {
        ...state,
        sending: false,
        lastError: data.errorMessage ?? "The assistant failed to answer.",
        messages: state.messages.map(message =>
          message.id === data.messageId
            ? {
                ...message,
                status: "failed",
                errorMessage: data.errorMessage ?? "The assistant failed to answer.",
                retryable: data.retryable !== false,
              }
            : message,
        ),
      };

    case "heartbeat":
      return state;

    default:
      return state;
  }
}

function upsertMessage(messages: ChatMessage[], next: ChatMessage): ChatMessage[] {
  const existingIndex = messages.findIndex(message => message.id === next.id);
  if (existingIndex === -1) {
    return [...messages, next];
  }

  return messages.map(message => (message.id === next.id ? next : message));
}
