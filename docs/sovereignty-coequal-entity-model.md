# Sovereignty model: customer & tenant as co-equal entities (the tenant-owned-edge rule)

**Status: resolved conceptual model, 2026-06-23.** Captured for DAV first, to be modeled into UDLM
(the data-model contract) and DCM (the architecture it gates). Backed by deep-research
(`deep-research/customer-tenant-coequal-2026-06-23`) which confirmed the pattern; refines the earlier
"strictly tenant-scoped customer" conclusion (`deep-research/tenancy-sovereignty-2026-06-21`,
uc-sov-004 v1).

## The model
**Customer and Tenant are co-equal entities** — neither contains the other. They are co-equal as
*entities* but **asymmetric in role**: the **tenant is the hard isolation boundary**; the **customer is
a boundary-spanning identity**. Their relationship is **M:N**:
- a **customer can have multiple tenants**, which remain isolated from each other (e.g. a multinational
  client engaged under several per-jurisdiction sovereign tenants);
- a **tenant can have multiple customers**, which are *not* isolated from each other within that tenant
  (customer is a demand/attribution dimension, **not** a nested isolation boundary), and any of those
  customers may also be associated with other tenants.

### The split that makes it work — identity vs relationship vs data
| layer | lives | holds |
|---|---|---|
| **Customer identity** (the co-equal anchor) | shared / control plane | a stable global ID + minimal, **non-relationship-revealing** attributes. **No tenant-mapping.** |
| **Customer↔Tenant relationship** (the edge) | **inside the tenant** | the fact that *this tenant* engages *this customer*. Tenant-scoped, so the fact-of-relationship obeys tenancy. |
| **Customer-attributed data** (demand, importance, context, M:N to use-cases) | **inside the tenant** | everything substantive, hard-siloed per tenant, never centrally consolidated. |

**The generalized primitive (for UDLM/DCM):** *a relationship between two co-equal entities is owned by —
and inherits the isolation of — the more-restrictive domain.* Customer↔tenant is the first instance;
the same rule applies to any cross-domain association whose *fact of association* is itself sensitive
(provider↔consumer, asset↔jurisdiction, …). The edge resolves into the stricter domain.

## Why the edge lives in the tenant (sovereignty)
Deep-research overrides (high-confidence):
1. **Fact-of-relationship is itself the protected secret** (Swiss banking secrecy lists the *existence*
   of the relationship first). So there must be **no shared surface that maps a customer to its
   tenants** — the mapping is the edge, and the edge is tenant-side. "All tenants for customer X"
   becomes a **deliberate, audited cross-tenant scan**, never a casual control-plane lookup. *Feature.*
2. **Even thin identifiers are sovereignty-sensitive** (cf. Cloudflare customer-metadata-boundary) —
   the shared identity anchor must carry no relationship-revealing metadata; where fact-of-relationship
   is protected, keep the anchor opaque/encrypted or omit it.
3. **Residency ≠ sovereignty** (CLOUD Act) — *who controls* the shared anchor governs, not where it sits.

## How it resolves the RBAC ⇄ sovereignty tension
The tension that surfaced this: global RBAC references the customer entity (wants it shared), while
sovereignty wants customer data siloed. The split resolves it:
- RBAC binds to the **bare customer-identity anchor's stable ID** (control plane). ✔ shared
- **Customer-scoped grants** (which bind a customer to a *project*, and a project belongs to a tenant —
  so the grant reveals customer↔tenant) live **tenant-side** with the edge. ✔ siloed
- Customer **data/context** lives tenant-side. ✔ siloed

So only the *bare identity + the role catalog* are shared; everything relationship- or data-bearing
inherits tenancy.

## Mapping to DAV / UDLM / DCM
- **DAV (today → target):** the current `customers` table conflates all three layers. Target: a slim
  `customer` identity anchor in control (`public`) carrying no tenant-mapping; the M:N
  customer↔use-case demand + `customer_projects` + customer-scoped RBAC grants stay tenant-side. (The
  immediate boot fix can use the interim `customers→control` reclassification to restore service; the
  identity/relationship/data split is the follow-on modeling.) See
  `tenancy-phase2-tenant-aware-runner.md`.
- **UDLM:** model Customer and Tenant as co-equal entity types; model the Customer↔Tenant relationship
  as a **tenant-owned edge** (its provenance/residency inherits the tenant), with a field-level marker
  that the edge is relationship-revealing → never replicated to a shared plane.
- **DCM:** gate it with use cases — `uc-sov-004` (revised), `uc-sov-008/009/010` in
  `dcm/dav/use-cases/sovereignty/`. The same set is mirrored into DAV (`dav/use-cases/sovereignty/`) as a
  **self-gating** corpus so DAV's own future changes are evaluated against the model it espouses (#184).
