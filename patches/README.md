# Owned auth sandbox patch

Source: MIT repository https://github.com/andrelima28439/ecommerce-microservices,
revision bae94402773bc7fd7d05dadd4062dffe2d1c2057,
auth-service/src/index.ts. See auth-rate-limit.patch for the exact one-line change.
The 100-request default remains; AUTH_RATE_LIMIT_MAX is constrained to 100–10000.
The sandbox overlay uses 6000 requests per 15 minutes. Only local isolated demo auth is
changed. Stripe/Cloudinary/Resend and the original checkout are unchanged.

```sh
chaosai sandbox
# After all runners exit and journals are resolved:
chaosai sandbox --restore
```

setup builds a derived image in .chaosai/sandbox-auth, using the existing scoped image's
installed dependencies. If the running image has been removed from Docker's image store,
a local snapshot preserves it first. No registry upload occurs. Generated source, overlay,
image identities and hashes stay in ignored .chaosai state. The compose override persists
for future chaosai up builds. Restore uses the preserved original image identity and removes
the active overlay marker; no data volume is removed. Both actions refuse active experiments,
KILL or pending/malformed journals. The CLI never applies this diff to the sibling checkout.

This local test configuration is part of run provenance: results assess the patched sandbox,
not the original auth rate-limiter capacity. Limiter capacity is not a chaos finding.
