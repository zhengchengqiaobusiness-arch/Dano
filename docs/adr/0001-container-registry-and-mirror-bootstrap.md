# Container registry and mirror bootstrap

Deployment images default npm and pnpm installs to `https://mirrors.cloud.tencent.com/npm/`, with `NPM_REGISTRY` as the supported override and `NPM_CONFIG_REGISTRY` kept only for compatibility. Runtime containers also set npm registry on startup so `npm config get registry` reflects the active environment without rebuilding the image.

The runtime image installs `ca-certificates` from the base Debian source before switching apt to Tencent mirrors. This requires two `apt-get update` calls: one before installing CA certificates from the original source, and one after rewriting apt sources because the package index source has changed. Switching to Tencent HTTPS apt mirrors before CA certificates are present fails certificate verification and leaves packages such as `ca-certificates`, `curl`, `python3`, and `bubblewrap` unavailable.

Registry changes are verified at three boundaries: the release script build argument, the build stage npm/pnpm config used for dependency installs, and the runtime container npm config used for operational inspection.
