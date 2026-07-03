# Dano Deploy Instructions

## Validation

- For Podman/deploy/runtime/Heimdall/bash/upload validation, run the full browser acceptance path, not only `smoke:deploy`.
- The minimum browser acceptance path is: plain text chat returns, image upload is read/described by the model, and the model triggers a successful `bash ls` tool call.
- After validation, stop and remove the test Compose stack, Dano temporary images/tags, and dangling build layers; keep reusable base images unless explicitly asked.
