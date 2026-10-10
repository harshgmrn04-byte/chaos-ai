# Application mapping and overrides

From chaos-ai with the virtual environment active:

```sh
chaosai map --mermaid
```

This reads TARGET_DIR/docker-compose.yml, applies chaos-ai/overrides.yaml when present,
prints node and dependency tables with evidence, and creates runs/<UTC timestamp>/graph.json
and .chaosai/graph.json. `--mermaid` also writes docs/graph.mmd. `--run-dir runs/<timestamp>`
updates an existing run instead of creating one. Output directories must remain inside
chaos-ai. `run` saves the same parsed graph in its run and latest locations.

Mapping uses no Docker, network or LLM. The map command reads configuration from exported
environment variables and config defaults, without opening chaos-ai/.env. Set TARGET_DIR and
COMPOSE_PROJECT in your shell if needed. Relative TARGET_DIR resolves from chaos-ai.
`map --offline` and `run --offline --yes` use the packaged source graph snapshot, apply any
overrides, and never read target files. Its sources.parsed is false because this invocation
did not parse Compose. The snapshot matches the corrected mapper plus shipped flow membership,
including ignored configuration and non-injectable isolated tools. Offline metrics/experiments
remain synthetic.

## Inference rules

Service IDs are Compose service names. Explicit container names, image and numeric host/container
ports are preserved. Ports support short/long forms, bind IPs, protocols, unpublished container
ports, and ranges; unresolved interpolated host ports cannot be resolved. Host/container port
lists are independent sorted lists, not a positional pairing table. Build-only services have
image=null and kind=app. Other kinds come from image repository names.

Edges point from the dependent service to its dependency. List/dict depends_on entries always
supply startup evidence. Environment list/dict values and build.args list/dict values supply
literal URL authority or plain host references. URL credentials, paths and queries never supply
host references. Service name and explicit container name resolve directly; localhost ports
resolve only against published host ports. Ambiguous references are omitted with a warning.
Unknown startup edges are refined by URL schemes when present; multiple protocols between the
same endpoints remain separate. Duplicate evidence is combined and sorted. Evidence stores
Compose field paths and resolution facts, never raw environment values or URL credentials.

CORS/allowed-origin variables are excluded before any reference or provider inference:
`FRONTEND_URL`, names ending in `_ORIGIN` or `_ORIGINS`, and names beginning with `CORS_`
(case-insensitive). Every excluded variable is recorded by its complete Compose field path in
`sources.ignored_config`, a sorted list of strings. Values are never recorded. For this target:

```json
"ignored_config": [
  "compose.services.auth-service.environment.FRONTEND_URL",
  "compose.services.order-service.environment.FRONTEND_URL",
  "compose.services.product-service.environment.FRONTEND_URL"
]
```

Tool nodes default to injectable=false when no dependency edge points to them, with
`injectable_reason: "Tool has no inbound dependency edges."` in graph.json. A tool's outbound
edges do not make it eligible. Tool defaults are evaluated after edge overrides, so removing
the last inbound edge disables it and adding one enables it. An explicit node override can
set injectable either way; its reason is marked as an override unless a custom reason is given.
These eligibility defaults never bypass the later Docker scope/policy/approval controls.

Stripe, Cloudinary and Resend variable-name tokens create external, non-injectable nodes and
potential dependency edges even when their configured values are placeholders. URL scheme
determines edge kind; a provider name or credential alone leaves kind=unknown. All inferred
criticality values are unknown. The graph does not prove an endpoint is used or currently works.

`env_file` supports string, list and long-form path declarations. Contents are **never opened**,
including optional files, in accordance with AGENTS.md's prohibition on reading .env. A warning
states that these dependencies need overrides. Bare pass-through environment names can identify
external providers, but do not read process values. Compose interpolation (`${...}`) is not
expanded; it cannot invent an internal edge. Private API keys are never needed for mapping.
The checked-in target has no env_file declarations, so this limitation omits no current edges.

## Override format

Every section is optional. Unknown fields and invalid enum values are rejected. Example:

```yaml
nodes:
  remove: [mailhog]       # also removes incident edges
  add:                   # full Node; same ID replaces the existing node
    - id: audit-tool
      container: null
      kind: tool
      image: null
      host_ports: []
      container_ports: []
      injectable: false
edges:
  remove:
    - source: frontend
      target: auth-service
      kind: http         # omit kind to match every protocol for this pair
  add:                   # same source/target/kind replaces evidence and criticality
    - source: frontend
      target: audit-tool
      kind: http
      evidence: "manual: inspected the audit integration configuration"
      criticality: soft # defaults to unknown when omitted
  criticality:
    - source: product-service
      target: redis
      kind: cache        # optional; omit to update every protocol for this pair
      criticality: hard
flows:
  - name: login
    path: [frontend, auth-service, postgres]
```

Order: remove nodes and incident edges, upsert nodes, remove edges, upsert edges, update
criticality, then merge flows by name. Each operation refers to the graph at that stage.
Remove/update selectors that match nothing fail to reveal typos. Edge endpoints and flow
participants must exist after merging; removing a node used by a flow requires replacing that
flow. External nodes must have no container and injectable=false. Stripe, Cloudinary and Resend
IDs cannot be repurposed as injectable local nodes. Overrides never authorize Docker operations.

For example, explicitly enabling isolated MailHog requires this full-node override:

```yaml
nodes:
  add:
    - id: mailhog
      container: ecommerce-mailhog
      kind: tool
      image: mailhog/mailhog
      host_ports: [1025, 8025]
      container_ports: [1025, 8025]
      injectable: true
      injectable_reason: "Manual choice to include MailHog in experiments."
```

Flow `path` means **components the journey touches, ordered from the user entry point to
the deepest dependency**. It is an ordered touch-set, not a call chain or an ordering of
runtime operations. Participants at equal depth may follow either order. Consecutive participants
need not have a direct graph edge, and flows do not add edges. Both the CLI and Mermaid render
`Flow <name> touches: a, b, c` as a participant list.

Membership was re-checked against target-notes sections 1–5 and the cited source:

| Flow | Components touched (entry point to deepest dependencies) | Evidence |
|---|---|---|
| browse | frontend, product-service, redis, postgres, mongodb | Section 2 product detail and section 4 cache behavior; productController.ts:87–110 reads MongoDB reviews after a cache miss and PostgreSQL lookup |
| login | frontend, auth-service, postgres | Sections 1–2, authController.ts:61–91 |
| checkout | frontend, order-service, postgres, rabbitmq, notification-service, resend, mongodb | Sections 3 and 5; orderCreated handler sends email then creates a MongoDB Notification record (orderConsumer.ts:33–61) |

The shipped override's membership and order are unchanged: MongoDB belongs in browse for
uncached product detail and in checkout through asynchronous notification storage. Both MongoDB
memberships were added to the older offline fixture, which previously omitted them.
Stripe is conditional and bypassed by Compose's placeholder, so it stays outside the default
checkout flow. Browse PostgreSQL/MongoDB participation depends on operation/cache state;
checkout notification/Resend work is asynchronous. Criticality overrides above are examples
of a human assessment, not measured findings; the shipped override leaves all unknown.

The cross-check against target-notes sections 1, 3, 4 and 5 is in
[mapper-cross-check.md](mapper-cross-check.md). Neither Compose nor target-notes was changed.
