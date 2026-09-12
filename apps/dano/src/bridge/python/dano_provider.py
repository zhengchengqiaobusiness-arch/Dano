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


def install_urllib():
    """Intercept standard-library openers only; never place OA credentials here."""
    import io
    import socket
    from email.message import Message
    from http.client import responses
    from urllib.request import BaseHandler, OpenerDirector
    import urllib.request
    from urllib.response import addinfourl

    origin = os.environ.get("DANO_PROVIDER_ORIGIN")
    if not origin:
        return

    def authority(url):
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or parsed.username is not None or parsed.password is not None:
            return None
        return (parsed.scheme, parsed.hostname, parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80))

    expected = authority(origin)
    class LoginHandler(BaseHandler):
        # Request processors have already run; intercept before proxy or HTTP
        # transport handlers, preserving urllib's request/response pipelines.
        handler_order = 0

        def http_open(self, req):
            try:
                matches = authority(req.full_url) == expected
            except ValueError:
                matches = False
            if not matches:
                return None
            return send_with_login(req)

        https_open = http_open

    def send_with_login(req):
        parsed = urlsplit(req.full_url)
        path = (parsed.path or "/") + (("?" + parsed.query) if parsed.query else "")
        body = req.data
        if isinstance(body, bytes):
            try:
                body = body.decode("utf-8")
            except UnicodeDecodeError:
                raise URLError("Dano OA requests currently require a UTF-8 request body") from None
        if body is not None and not isinstance(body, str):
            raise URLError("Dano OA requests currently require a buffered UTF-8 request body")
        try:
            result = request(req.get_method(), path,
                             headers=dict(req.header_items()), body=body,
                             timeout=15.0 if req.timeout is socket._GLOBAL_DEFAULT_TIMEOUT else req.timeout)
        except ProviderError as exc:
            raise URLError("%s: %s" % (exc.code, exc)) from None
        headers = Message()
        for name, value in result["headers"].items():
            headers[name] = value
        status = result["status"]
        stream = io.BytesIO(result["body"].encode("utf-8"))
        # Broker requests never follow redirects. Do not let a new opener forward
        # either the Skill's old identity or a Dano identity to a redirect target.
        if not 200 <= status < 300:
            raise HTTPError(req.full_url, status, responses.get(status, ""), headers, stream)
        response = addinfourl(stream, headers, req.full_url, status)
        response.msg = response.reason = responses.get(status, "")
        return response

    original_init = OpenerDirector.__init__

    def init_with_login(self):
        original_init(self)
        self.add_handler(LoginHandler())

    OpenerDirector.__init__ = init_with_login
    if urllib.request._opener is not None:
        urllib.request._opener.add_handler(LoginHandler())
