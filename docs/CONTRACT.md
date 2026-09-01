# Capability contract

Each capability is machine-readable and includes:

- identity: `id`, `title`, `description`, `operation`;
- transport: method + normalized URL template;
- `inputSchema` and `outputSchema` inferred from evidence;
- `inputForm` with widgets and observed candidate values;
- evidence references;
- validation state;
- side-effect/confirmation policy;
- completion criteria;
- explicitly approved data bindings.

Automatic composition is only allowed when a binding has:

```json
{
  "approved": true,
  "fromCapabilityId": "customer-search",
  "fromPath": "$.items[0].id",
  "toPath": "$.customerId"
}
```

If a binding is absent, not approved, or produces multiple possible values, the planner must ask the user.
