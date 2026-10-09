# Dano Deploy Instructions

## Validation

- For Podman/deploy/runtime/Heimdall/bash/upload validation, run the full browser acceptance path, not only `smoke:deploy`.
- The minimum browser acceptance path is: plain text chat returns, image upload is read/described by the model, and the model triggers a successful `bash ls` tool call.
- Follow the deployment target and acceptance mode in the root `AGENTS.md`. Direct deployment acceptance uses the existing service URL and OAuth configuration and leaves that service running.
- For explicitly requested isolated acceptance, remove only this run's temporary stack, images and unreferenced build layers; retain reusable base images and existing service data.
