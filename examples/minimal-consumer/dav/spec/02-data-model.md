# 02 — Data Model

## Entities

### User

```
User:
  id: uuid                           (primary key)
  email: string                      (unique)
  password_hash: string              (bcrypt; see 03-authentication.md)
  tier: free | pro | admin           (default: free)
  created_at: timestamp
  last_login_at: timestamp | null
  is_suspended: boolean              (default: false)
```

### ShortURL

```
ShortURL:
  id: uuid                           (primary key)
  alias: string                      (unique; the short form)
  destination: string                (the long URL)
  owner_id: uuid                     (foreign key to User)
  created_at: timestamp
  is_disabled: boolean               (default: false)
```

### AccessEvent

```
AccessEvent:
  id: uuid                           (primary key)
  short_url_id: uuid                 (foreign key to ShortURL)
  timestamp: timestamp
  source_ip_hash: string             (SHA-256 of source IP; for analytics without PII)
  user_agent_family: string          (browser family; "Chrome", "Firefox", etc.)
```

## Relationships

- One `User` owns many `ShortURL`s (cascade: suspending a user disables all their URLs)
- One `ShortURL` has many `AccessEvent`s (cascade: disabling a URL does not delete events)

## Derived data

Analytics views are computed on query; not stored. For a given `ShortURL`:

- Total access count → `SELECT COUNT(*) FROM AccessEvent WHERE short_url_id = ?`
- Access by day → grouping of `AccessEvent.timestamp`
- Top user agent families → aggregation of `AccessEvent.user_agent_family`

## Quota enforcement

`User.tier` determines short-URL creation quota:

- `free`: 100 short URLs per user (lifetime)
- `pro`: 10,000 short URLs per user
- `admin`: unlimited

Quota is enforced at short-URL creation time by counting `ShortURL`s owned by the user (excluding disabled ones).

## Notes

- Deletion is soft (`is_disabled: true`), not hard. Historical analytics remain queryable.
- IP addresses are hashed for privacy; the hash function uses a per-deployment salt stored outside the database.
