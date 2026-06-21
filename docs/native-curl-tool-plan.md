# Native Curl Tool Plan

## Purpose

Provide a native `curl` tool to an Agent runtime that does not expose a shell.
It is a process adapter, not a REST or HTTP abstraction.

## Contract

```ts
type CurlInput = {
  args: string[];
};
```

- Tool name: `curl`.
- Spawn the installed `curl` executable directly with `shell: false`.
- Forward every `args` element unchanged and in order.
- Use the active Agent workspace as the curl process working directory.
- Inherit the Dano process environment unchanged; do not load workspace shell
  activation files.
- Return curl stdout unchanged as tool `content`.
- Preserve curl stderr and exit code unchanged in tool `details`.
- A non-zero curl exit remains a process result; only failure to start curl is
  a tool failure.
- Kill the curl process if the Agent aborts the tool call.

## Explicitly Not Included

- HTTP method, URL, header, query, body, authentication, redirect, timeout,
  response-size, filesystem, or network policy.
- Argument validation beyond the tool schema requiring `string[]`.
- JSON parsing, status-code interpretation, output encoding conversion,
  retries, logging, redaction, or timing.
- Axios, native `fetch`, shell commands, curl config generation, or stdin
  plumbing.

All curl behavior is selected by the Agent through ordinary curl arguments.
Binary output must use curl's own file-output arguments.

## Agent Guidance

Tool description:

```text
Run curl with the provided CLI arguments.
```

Prompt guidelines:

- Use normal curl CLI arguments, but omit the `curl` executable name.
- Put each shell argument into one `args` item and omit shell quote characters
  and shell operators. Values themselves remain unchanged.
- Use curl's own options for files and output; relative paths resolve from the
  active Agent workspace.

Example conversion:

```text
curl -X POST -H 'content-type: application/json' -d '{"ok":true}' https://example.com
```

becomes:

```json
{
  "args": [
    "-X",
    "POST",
    "-H",
    "content-type: application/json",
    "-d",
    "{\"ok\":true}",
    "https://example.com"
  ]
}
```

## Implementation

Add one bridge tool module using `node:child_process.spawn` and register it as
a custom tool only in the standalone/detached session path at
`packages/bridge/src/detached-session.ts`. Do not add plugin, MCP, or other
runtime adapters. Remove the existing `bash` tool registration and its import.

Because the shell command prefix exists only for `bash`, also remove
`buildDetachedShellCommandPrefix`, `buildWorkspaceActivationPrefix`, their
bash-only helper code, and their tests. Keep workspace environment detection,
which is still used by the UI bridge.

Add no dependency, config loader, policy layer, or HTTP client.

Install the Debian `curl` package in the runtime stage of `Dockerfile` and
remove apt lists in the same layer. The build stage does not need curl.

## Checks

1. Arguments are forwarded unchanged and in order.
2. Spaces and shell metacharacters remain literal argument content.
3. Stdout is returned unchanged.
4. Stderr and a non-zero exit code are preserved.
5. Spawn failure becomes a tool failure.
6. Agent abort terminates curl.

Run:

```bash
pnpm run check
pnpm test
pnpm run build
```
