# Minimal DAV Consumer — "TinyURL"

This is a minimal, synthetic DAV consumer. It demonstrates the integration surface with a small enough scope that you can read every file in one sitting and understand how the pieces fit together.

The subject is a fictional URL-shortener service called **TinyURL**. It has none of the complexity of a real DCM-style architecture — the point is to show the *shape* of a DAV consumer, not to model a realistic product.

## What's here

```
minimal-consumer/
├── README.md                   (this file)
├── dav-version.yaml            DAV version compatibility declaration
├── dav/
│   ├── spec/                   Spec corpus (4 docs)
│   │   ├── 01-overview.md
│   │   ├── 02-data-model.md
│   │   ├── 03-authentication.md
│   │   └── 04-operations.md
│   ├── use-cases/              3 use cases exercising all UC types
│   │   ├── README.md           Vocabulary declaration + UC organization
│   │   ├── authentication/
│   │   │   └── login-flow.yaml             (analytical UC)
│   │   └── spec_integrity/
│   │       ├── all-docs-exist.yaml         (assertion UC)
│   │       └── auth-spec-complete.yaml     (hybrid UC)
│   ├── prompts/                Prompt slot content
│   │   ├── consumer_overview.md
│   │   ├── domain_terminology.md
│   │   ├── doc_corpus_layout.md
│   │   └── out_of_scope.md
│   ├── calibration/            Reference analyses for predictable-correctness scoring
│   │   └── login-flow.yaml
│   ├── assertions/             Python modules for assertion UCs
│   │   ├── __init__.py
│   │   ├── doc_existence.py
│   │   └── auth_spec_check.py
│   └── stage-config/
│       └── stages.yaml         Pipeline configuration
```

## What this consumer does NOT do

- Does not have a real product behind the spec — the "TinyURL" system is fictional
- Does not run against real inference infrastructure — this is content only, no deployment
- Does not exercise every corner of the DAV schemas — only the common cases
- Does not represent a recommended architecture — the subject is chosen for simplicity

## Why it's useful

Three things:

1. **Tutorial** — a new consumer can read this end to end and understand what's required before starting their own content
2. **Smoke test** — the assertion UCs can be run by DAV deployed against this consumer to verify the framework works
3. **Schema stress test** — by authoring against the spec 05 and spec 07 contracts, this example surfaces problems with those contracts before they bind real consumers

## How to read this

In order:

1. `dav-version.yaml` — declares which DAV version this content targets
2. `dav/spec/` — read all four docs; they're small
3. `dav/use-cases/README.md` — see the vocabulary and organization
4. `dav/use-cases/authentication/login-flow.yaml` — an analytical UC
5. `dav/use-cases/spec_integrity/all-docs-exist.yaml` + the assertion module — an assertion UC
6. `dav/use-cases/spec_integrity/auth-spec-complete.yaml` — a hybrid UC
7. `dav/prompts/` — what the consumer tells DAV about its own domain
8. `dav/calibration/login-flow.yaml` — what a "correct" analysis looks like (human-authored)
9. `dav/stage-config/stages.yaml` — pipeline declaration

This consumer serves as the end-to-end smoke test invoked after engine changes — a tiny synthetic consumer with no domain dependencies that exercises the full DAV pipeline.
