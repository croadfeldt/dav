---
marp: true
theme: default
paginate: true
---

# DCM 6-Week Roadmap — Pipeline Use Cases

**25 UCs tell one continuous story: authenticate → bootstrap → provision → operate → rebuild → portability**

```
Act 0: AUTHENTICATE           Act 1: BOOTSTRAP              Act 2: PROVISION
  Keycloak token → DCM          PXE → cluster → CP            Composite → catalog
  identity resolved             deployed on containers         → decompose → validate
       │                              │                        → realize full stack
       └──────────────┬───────────────┘                              │
                      ▼                                              ▼
              Act 3: OPERATE                              Act 4: REBUILD (headline)
              drift → remediate                           destroy → derive plan
              re-apply → no-op                            → re-evaluate → rebuild
                                                          → RTO measured
                                                                │
                                                                ▼
                                                       Act 5: PORTABILITY
                                                       provider fails → re-resolve
                                                       → rebuild on alternate
```

**14 new + 11 existing = 25 pipeline UCs | Trifecta: seed + capability + data-model**

---

# Weekly Deliverables

| Week | Act | Pipeline UCs | Key Milestone |
|------|-----|-------------|---------------|
| **wk2** | 0, 1 | actor-authentication, bare-metal-pxe-bootstrap, cluster-bootstrap, control-plane-deployment, four-state-store-conformance | Control plane operational on bare metal |
| **wk3** | 2 | composite-service-to-catalog-item, composite-service-provision, sovereignty-validation-policy, + vm-standard-provision, tenant-onboarding, policy-scope-boundary | Full provision arc from composite service |
| **wk4** | 3, 4 | drift-detection-remediation, idempotent-reconvergence, dynamic-rehydration, rehydration-rto-measurement, + provider-failure | **Headline: destroy and rebuild from data model** |
| **wk5** | 5 | provider-portable-rebuild, profile-based-deployment, + audit-merkle-tree, policy-override | Portability + hardening (stretch) |

**Architecture gaps:** Authz undecided (Authorino/Kessel/OPA) | Common taxonomy repo needed | OSAC = Red Hat-only

**Flow:** UC → capability → workstream → Jira → milestone delivery (Piotr's model)
