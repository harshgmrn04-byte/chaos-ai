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
`map --offline` and `run --offline --yes` use the packaged Phase 1 graph fixture, apply any
overrides, and never read target files. Its sources.parsed is false; its evidence and external
HTTP kinds were manually authored in Phase 1. Offline metrics/experiments remain synthetic.

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
    - source: auth-service
      target: frontend
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

Flow paths list journey participants; consecutive participants need not have a direct graph
edge. They describe branches, cache misses and asynchronous work rather than a sequence of
network calls. They do not add edges. The shipped example defines browse (including MongoDB
reviews for detail), login, and checkout (DB persistence, broker, notification email/store).
Stripe is conditional and bypassed by Compose's placeholder, so it stays outside the default
checkout flow. Browse PostgreSQL/MongoDB participation depends on operation/cache state;
checkout notification/Resend work is asynchronous. Criticality overrides above are examples
of a human assessment, not measured findings; the shipped override leaves all unknown.

The cross-check against target-notes sections 1, 3, 4 and 5 is in
[mapper-cross-check.md](mapper-cross-check.md). Neither Compose nor target-notes was changed.
