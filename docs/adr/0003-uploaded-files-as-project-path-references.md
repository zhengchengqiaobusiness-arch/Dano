# ADR 0003: Treat uploaded files as project path references

Status: Accepted
Date: 2026-06-29

Browser uploads are stored under the current session workspace instead of remaining temporary RPC payloads. The UI shows the user's original filename in the attachment area, while the bridge passes the uploaded file's workspace-relative path to Pi so the file is handled like any other project file.

Keeping `files` as a protocol field lets the browser track upload state, while `BridgeRpcAdapter` resolves those refs into project path references. For image uploads, it also reads the stored image bytes into model image content so vision-capable models can inspect the image directly.

Before upload, the browser attempts to encode static raster images as WebP at their original pixel dimensions. It uses the result only when it is smaller than the original. Animated images, SVG, existing WebP, and images the browser cannot decode or encode remain unchanged. The original file must satisfy the upload size limit before conversion. Only the chosen bytes are uploaded; their hash, MIME type, size, and workspace filename extension describe those bytes, while the UI keeps the user's original filename for display.
