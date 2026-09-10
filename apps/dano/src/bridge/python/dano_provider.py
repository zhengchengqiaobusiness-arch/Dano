"""Dano's execution-scoped provider client. No provider credentials enter Python."""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener, HTTPRedirectHandler


class ProviderError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(method, path, headers=None, body=None, timeout=15.0):
    """Return the Broker envelope: ok/status/headers/body, or raise ProviderError."""
    endpoint = os.environ.get("DANO_PROVIDER_URL", "")
    capability = os.environ.get("DANO_PROVIDER_CAPABILITY", "")
    target = urlsplit(endpoint)
    if (target.scheme != "http" or target.hostname != "127.0.0.1"
            or target.path != "/request" or not capability):
        raise ProviderError("authentication_required", "Run this Skill inside an authenticated Dano Assistant Turn.")
    payload = {"method": method, "path": path}
    if headers is not None:
        payload["headers"] = headers
    if body is not None:
        payload["body"] = body
    req = Request(endpoint, data=json.dumps(payload).encode("utf-8"), method="POST",
                  headers={"Authorization": "Bearer " + capability, "Content-Type": "application/json"})
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(req, timeout=timeout) as response:
            result = json.load(response)
    except (HTTPError, URLError, OSError, ValueError):
        raise ProviderError("provider_request_failed", "Dano provider request unavailable or execution ended.") from None
    if not result.get("ok"):
        error = result.get("error", {})
        raise ProviderError(error.get("code", "provider_request_failed"),
                            error.get("message", "Provider request failed."))
    return result
