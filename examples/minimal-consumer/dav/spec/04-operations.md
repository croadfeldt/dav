# 04 — Operations

## 4.1 Short URL creation

1. Authenticated user sends `POST /api/urls` with `{destination: <long-url>}`
2. System verifies user is not suspended (else 403)
3. System checks quota: if owned-non-disabled count ≥ tier limit, return 402
4. System generates a unique 8-character alias using base62 encoding of a random 48-bit integer; retry on collision (extremely rare)
5. System creates `ShortURL` record
6. System returns `{alias: <string>, short_url: <full url with alias>}`

## 4.2 Short URL resolution

1. Anyone sends `GET /<alias>`
2. System looks up `ShortURL` by alias
3. If not found, return 404
4. If `is_disabled`, return 410 (Gone)
5. If `User.is_suspended` for the URL's owner, return 410
6. System records `AccessEvent` (async; does not block redirect)
7. System returns 302 redirect to `destination`

## 4.3 Analytics

1. Authenticated user sends `GET /api/urls/<alias>/analytics`
2. System verifies the URL is owned by the user (or user is admin)
3. System queries `AccessEvent` aggregates (total count, access by day, top user agents)
4. Returns aggregates; no individual `AccessEvent` records are exposed

## 4.4 User suspension

1. Administrator sends `POST /api/admin/users/<user_id>/suspend`
2. System sets `User.is_suspended = true`
3. System invalidates active sessions for the user
4. System cascades: all owned `ShortURL`s effectively become inaccessible (return 410 on resolution)

Suspension is reversible (`POST /api/admin/users/<user_id>/unsuspend`). Unsuspension restores URL access.

## 4.5 URL deletion

1. Owner sends `DELETE /api/urls/<alias>`
2. System verifies ownership (or admin)
3. System sets `ShortURL.is_disabled = true`
4. Historical `AccessEvent` records are retained indefinitely

There is no hard-delete path. This is by design — analytics data is retained for the service lifetime.

## 4.6 Quota reconciliation

Quota is counted at creation time by SQL query. There is no cached counter. This is simple and always correct but has O(n) cost per creation.

For users with many URLs, this may be slow. Acceptable for v1.0; a cached counter is a v1.1 optimization.

## 4.7 Scheduled maintenance

No scheduled maintenance operations are defined in v1.0. Database growth is unbounded (AccessEvent records never purged); this is a known scaling concern deferred to v1.1.
