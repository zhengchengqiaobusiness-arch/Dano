# Architecture

```text
Real browser
  │
  ├─ UI event recorder ────────────────┐
  └─ request/response recorder ────────┤
                                      ▼
                               Evidence JSONL
                                      │
                         deterministic correlation
                                      │
                                      ▼
                         Atomic capability candidates
                          │                      │
                 heuristic classifier     OpenAI refinement
                          └──────────┬───────────┘
                                     ▼
                             Editable catalog
                                     │
                               evidence validator
                                     ▼
                           verified capabilities
                              │             │
                       policy planner   Skill exporter
                              │             │
                        guarded execution  self-contained package
```

## Design boundary

The LLM is not the source of truth for endpoints, fields, options, response shape, or evidence.
It can:
- name and describe observed behavior;
- classify ambiguous POST operations;
- propose a route plan among already-known capabilities;
- propose bindings, which remain disabled until approved.

The deterministic layer:
- captures evidence;
- redacts secrets;
- derives schemas from observed values;
- checks evidence IDs;
- gates publication;
- requires write confirmation;
- forbids unapproved automatic bindings.
