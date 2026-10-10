# Phase 3 source cross-check

Compared the generated default graph (Compose plus shipped flow-only overrides) against
docs/target-notes.md sections 1, 3, 4 and 5. This is source evidence, not a live dependency test.
The corrected graph has 13 nodes, 14 edges and 3 flows. Every edge carries a Compose field path; every
criticality is unknown. The Compose fixture is a byte-for-byte copy of the target file.

## Matches

| Notes | Generated graph |
|---|---|
| Section 1: ten services, container names and all published ports | All match, including auth host 3004/container 3001, Redis 6380/6379, PostgreSQL 5433/5432, no notification published port |
| Section 1: auth depends on PostgreSQL | auth-service → postgres, sql; healthy startup evidence plus DATABASE_URL |
| Sections 1 and 4: product depends on PostgreSQL, MongoDB, Redis | product-service → postgres/sql, mongodb/nosql, redis/cache |
| Sections 1 and 3: order persists via PostgreSQL and publishes via RabbitMQ | order-service → postgres/sql, rabbitmq/amqp |
| Sections 1 and 5: notification consumes RabbitMQ and stores in MongoDB | notification-service → rabbitmq/amqp, mongodb/nosql |
| Section 1: frontend depends on auth, product, order | Three outgoing frontend edges; build.args supply http scheme and published localhost port evidence |
| Section 3: order creates no product/auth-service request | Neither direct order-service → product-service nor order-service → auth-service is invented |
| Section 5: Resend sends email; MailHog is infrastructure only | notification-service → resend exists; mailhog is an isolated tool node, with no incoming/outgoing edges, default injectable=false and a reason |

## Every dependency difference and limit

1. **product-service → cloudinary (unknown)** is additional to the named dependency lists
   in the requested sections (section 4 mentions uploads without naming their provider).
   Compose's CLOUDINARY_* names declare it. Target-notes section 2 explicitly documents
   Cloudinary uploads, so it agrees with the fuller source notes, but is conditional on uploads.
2. **order-service → stripe has kind unknown**, because STRIPE_* names supply no URL scheme.
   Section 3 describes a remote payment API, conditional on key/paymentMethod and bypassed by
   the Compose placeholder. A static provider edge does not encode that activation condition.
3. **notification-service → resend has kind unknown**, because RESEND_API_KEY supplies no URL
   scheme. Section 5 describes the Resend email SDK/API. Presence does not prove delivery,
   and the graph cannot encode caught failures being stored as sent and acknowledged.
4. **product-service → cloudinary also has kind unknown**, because CLOUDINARY_* names supply
   no URL scheme. The offline snapshot now matches this inferred kind.

The prior auth/product/order → frontend CORS edges have been removed following review.
All three services' environment.FRONTEND_URL field paths are recorded in sources.ignored_config;
the variable values are not retained. CORS_*, *_ORIGIN and *_ORIGINS configuration is likewise
excluded. These variables set allowed origins and are not runtime dependencies.

There are no missing local dependency pairs, container-name mismatches or port mismatches.
Frontend URLs are build args rather than runtime environment fields; using them for evidence
is an explicit extension of environment inference, agreeing with section 1 and section 7's
browser context. These edges describe browser requests and do not prove container-local
localhost routing. Dependency arrows point toward required resources: notification → RabbitMQ
does not reverse the documented broker-to-consumer event delivery; checkout's flow lists touched
components ordered from entry point to deepest dependencies without inventing a direct
order → notification edge. CLI/Mermaid flow labels use "touches: a, b, c", without flow arrows.

Flow membership re-check: browse includes MongoDB because uncached product detail reads Review
(productController.ts:87–110, target-notes section 2); checkout includes MongoDB through the
notification service's NotificationModel.create after its email attempt (orderConsumer.ts:33–61,
target-notes section 5). Login touches PostgreSQL through auth. No shipped override membership
or ordering changed. The offline fixture gained MongoDB in browse and checkout to match them.

Section 1's three logical PostgreSQL databases and MongoDB collections collapse into their
container nodes; credential/database URL suffixes are not stored. Startup-only dependencies
and operation-specific/cache-dependent use share dependency edges. Healthy startup conditions
remain in evidence, but do not become hard criticality. Sections 3–5's payment bypass, cache
fallback absence, retry/reconnect, delivery and failure semantics are not expressible in the
section 4 Graph contract. These are representation limits, retained in target-notes for later
experiments, not corrections or claims of measured behavior. The shipped flows document their
branch/asynchronous meaning in overrides.yaml and docs/overrides.md.

The CORS removals, tool eligibility change and offline snapshot corrections are explicit above.
No target source file or source note was changed.
Future human corrections should carry manual evidence in overrides.yaml.
