# Document Corpus Layout

The TinyURL spec is four markdown documents, each covering one aspect of the system:

- `01-overview.md` — purpose, users, core operations, system boundaries
- `02-data-model.md` — entities (User, ShortURL, AccessEvent), relationships, quota rules
- `03-authentication.md` — registration, login, session, password reset (deferred), rate limiting
- `04-operations.md` — lifecycle operations including creation, resolution, analytics, suspension, deletion

Each document is self-contained but may reference others (e.g., Doc 04 references the entities defined in Doc 02).

When searching for specific topics:

- User identity and authentication → Doc 03
- What data is stored → Doc 02
- What operations are available → Doc 04
- High-level purpose and scope → Doc 01
