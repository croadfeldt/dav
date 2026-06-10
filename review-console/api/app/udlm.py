"""UDLM (Universal Data Lifecycle Model) — the data-model contract DAV operates against.

DAV realizes UDLM for architecture / capability *knowledge* (a peer to DCM's
infrastructure realization). This module is the single source of the UDLM contract
*version* DAV conforms to. Structured outputs (e.g. a model-extracted assessment) and the
stored Knowledge-family entities (Capability, TaxonomyTerm, Assessment, Finding, …) are
stamped with this version so producers/consumers can reason about compatibility.

Formalizing the full versioned UDLM schema/contract (entity shapes, field provenance,
lifecycle states, the four data states) and validating all DAV data against it is tracked
as its own effort — this constant is the seam everything hangs off of in the meantime.
See udlm/ docs and the UDLM-version requirements task.
"""

UDLM_VERSION = "0.1.0-draft"
