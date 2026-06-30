# Field Assist uses a transient Pi session

Field Assist runs as a one-shot Pi SDK session backed by `SessionManager.inMemory()` with tools and extension/resource side effects disabled, not as a live Dano turn, `answer_question` response, or normal detached session. This keeps field rewriting/generation aligned with the current Pi model configuration while preventing transcript, session list, tool, and extension UI pollution.
