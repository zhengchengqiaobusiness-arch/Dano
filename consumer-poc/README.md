# Self-contained skill consumer POC

This project is independent from Dano and `skillfrontend`. It needs Python plus `httpx`, an exported package directory, and runtime authentication through `DANO_AUTH_HEADERS` (or another authentication source documented by that package).

List the published capabilities and their input schemas:

```text
python consumer.py list <export-dir>/<package>
```

Execute a capability. Write capabilities automatically run their matching read-back verifier and return one JSON result containing both execution and verification:

```text
python consumer.py run <export-dir>/<package> <capability> --input-json '{"field":"value"}'
```

Another frontend can integrate in either of two ways:

1. Direct scripts: invoke `scripts/<capability>.py --input-json ...`, then `scripts/verify_<capability>.py` for writes. Treat a nonzero exit or `ok: false` as failure.
2. Schema wrapper: read `references/CONTRACT.json`, render a form from each capability's `input_schema`, call `consumer.py run`, and show the returned `execution` and `verification` objects. No Dano service or LLM is involved at runtime.
