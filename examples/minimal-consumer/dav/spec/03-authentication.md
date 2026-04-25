# 03 — Authentication

TinyURL uses self-hosted password authentication. No third-party identity providers.

## 3.1 Registration

1. User submits email and password via `POST /api/auth/register`
2. System validates: email is syntactically valid, password meets minimum strength
3. System hashes password using bcrypt (cost factor 12)
4. System creates `User` record with `tier=free`
5. System returns success; user proceeds to login

## 3.2 Login

1. User submits email and password via `POST /api/auth/login`
2. System looks up User by email
3. If `is_suspended`, return 403 without checking password
4. Verify password against `password_hash`
5. On success: update `last_login_at`, issue a signed session cookie
6. On failure: return 401 (no distinction between "wrong email" and "wrong password")

## 3.3 Session

- Session cookies are signed with a server-side secret (not JWT)
- Session validity: 7 days from issue
- Sessions are invalidated on logout (`POST /api/auth/logout`) or suspension

## 3.4 Password reset

Password reset is **not implemented in v1.0**. Users who lose their password must contact an administrator to reset it manually.

This is a known gap. It is not a bug — it is a deliberate v1.0 scope reduction. A proper reset flow (email-based, time-limited tokens) is planned for v1.1.

## 3.5 Rate limiting

Login attempts are rate-limited: 5 attempts per email per 15 minutes. Exceeding the limit returns 429 with a retry-after header.

Registration is rate-limited: 3 accounts per source IP per 24 hours.

## 3.6 Administrator authentication

Administrators use the same login flow as registered users. They are distinguished only by `User.tier == 'admin'`. There is no separate admin login endpoint.

An administrator's session carries the same cookie shape as a registered user's; admin privileges are checked on each API call that requires them (not baked into the session token).
