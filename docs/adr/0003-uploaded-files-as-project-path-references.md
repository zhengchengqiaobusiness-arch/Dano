# ADR 0003: Treat uploaded files as project path references

Status: Accepted
Date: 2026-06-29

Browser uploads are stored under the current session workspace instead of remaining temporary RPC payloads. The UI shows the user's original filename in the attachment area, while the bridge passes the uploaded file's workspace-relative path to Pi so the file is handled like any other project file.

This replaces the previous image-only upload path that converted uploaded files into base64 image content. Keeping `files` as a protocol field still lets the browser track upload state, but `BridgeRpcAdapter` now resolves those refs into project path references rather than image blocks.
