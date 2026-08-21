# ADR 0007: Do not equate OAuth token revocation with browser session logout

Status: Accepted
Date: 2026-08-20

## Context

Dano and an OAuth provider own separate authentication state. A successful
Authorization Code exchange proves one provider authorization response and lets
Dano create one Dano Login Session with one encrypted Provider Credential. It
does not give Dano ownership of the Provider Browser Session.

OAuth 2.0 token revocation and introspection operate on tokens. Browser login
state and logout notification require a separate provider contract, such as
OpenID Connect Session Management, RP-Initiated Logout, Front-Channel Logout,
or Back-Channel Logout. These OpenID Connect contracts depend on provider
metadata and artifacts such as an ID Token, `session_state`, `sid`, a session
iframe, or a signed logout token; they cannot be reconstructed from an OAuth
access token.

The currently verified OA contract is OAuth 2.0 rather than OpenID Connect. It
offers Authorization Code, refresh, a provider-specific token check, and a
provider-specific authenticated token deletion endpoint. It does not expose
OpenID Provider discovery, ID Tokens, session state, an end-session endpoint,
front-channel logout, or back-channel logout. OA browser logout deletes the OA
browser's own token; it does not revoke Dano's independently issued Provider
Credential.

## Decision

Keep these ownership boundaries explicit:

- OA owns the Provider Browser Session in the real OA authorization origin.
- OA issues and Dano consumes each one-time Authorization Code.
- Dano owns each opaque Dano Login Session and its HttpOnly Cookie.
- Each Dano Login Session owns its encrypted Provider Credential server-side.
- A configured revocation call ends only the Provider Credential Dano presents.
- Logout Propagation exists only when the provider implements and configures an
  explicit session/logout protocol; absence of that contract is reported as an
  unsupported capability, not hidden behind polling or token copying.

Dano will not put provider tokens, client secrets, or provider management
credentials in browser code. It will not poll the authorization page, copy an
OA browser token, embed a same-origin OAuth relay, or describe token revocation
as browser-session logout.

## Consequences

- An already logged-in OA user can complete Dano's explicit login flow without
  another credential prompt, but OA login alone cannot passively create a Dano
  Login Session.
- Dano login through OA can establish both systems' login state because the
  browser visits OA's authorization origin.
- OA browser logout does not end an existing Dano Login Session.
- Dano logout removes its Login Session and revokes its Provider Credential when
  configured, but it does not end the OA browser's independent login state.
- Full bidirectional logout requires OA to implement a standard session/logout
  contract and bind its browser and OAuth grants to that contract. Dano support
  should be added only against that verified contract and its signed identifiers.

## Evidence

- [OpenID Connect Session Management 1.0](https://openid.net/specs/openid-connect-session-1_0.html)
- [OpenID Connect RP-Initiated Logout 1.0](https://openid.net/specs/openid-connect-rpinitiated-1_0.html)
- [OpenID Connect Front-Channel Logout 1.0](https://openid.net/specs/openid-connect-frontchannel-1_0.html)
- [OpenID Connect Back-Channel Logout 1.0](https://openid.net/specs/openid-connect-backchannel-1_0.html)
- [RFC 7009 OAuth 2.0 Token Revocation](https://www.rfc-editor.org/rfc/rfc7009.html)
- [RFC 7662 OAuth 2.0 Token Introspection](https://www.rfc-editor.org/rfc/rfc7662.html)
- `apps/dano/src/__tests__/oauth-login-http.test.ts` verifies one Login Session
  logout, Credential revocation, independent same-User Login Sessions, and
  logout completion when provider revocation fails.
