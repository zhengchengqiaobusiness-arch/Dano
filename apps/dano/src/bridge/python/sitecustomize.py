"""Execution-local instrumentation, loaded by Python without changing a Skill."""
import importlib.machinery
import importlib.util
import os
import sys

# Preserve the deployment's existing customization, excluding only this staged
# directory. The instrumentation is installed last, including on startup errors.
try:
    directory = os.path.realpath(os.path.dirname(__file__))
    paths = [p for p in sys.path if os.path.realpath(p) != directory]
    spec = importlib.machinery.PathFinder.find_spec("sitecustomize", paths)
    if spec and spec.loader:
        existing = importlib.util.module_from_spec(spec)
        sys.modules["sitecustomize"] = existing
        spec.loader.exec_module(existing)
finally:
    try:
        from dano_provider import install_urllib
        install_urllib()
    except Exception:
        # Python normally ignores sitecustomize failures. A broken auth hook must
        # not silently send the package's original authentication instead.
        sys.stderr.write("Dano Python authentication initialization failed\n")
        os._exit(78)
