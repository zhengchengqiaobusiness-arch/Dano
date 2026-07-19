"""Shared authentication helpers for Playwright recording sessions."""

from __future__ import annotations

import json


_COMMON_TOKEN_KEYS = (
    "Admin-Token",
    "satoken",
    "token",
    "access_token",
    "accessToken",
    "Authorization",
    "jwt",
    "X-Token",
)


async def apply_token_auth(
    context,
    *,
    token: str,
    url: str,
    token_key: str | None = None,
) -> None:  # noqa: ANN001
    """Inject a raw token into cookies and localStorage before navigation."""
    if not token or not url:
        return

    from urllib.parse import urlparse

    host = urlparse(url).hostname
    keys = [token_key] if token_key else list(_COMMON_TOKEN_KEYS)
    for key in keys:
        if host:
            try:
                await context.add_cookies([
                    {"name": key, "value": token, "domain": host, "path": "/"},
                ])
            except Exception:  # noqa: BLE001
                pass
        await context.add_init_script(
            f"try{{localStorage.setItem({json.dumps(key)},{json.dumps(token)});}}catch(e){{}}",
        )
