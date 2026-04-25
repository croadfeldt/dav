# DAV Specification 01 — Framework Overview

**Status:** Stub (not yet authored)
**Audience:** New DAV user or prospective consumer
**Depends on:** None

## Purpose

This document is the reader's entry point into DAV. It explains what DAV is, what problems it solves, what a consumer looks like, what DAV produces, and how to read the rest of the specs.

Topics this spec will cover when authored:

- What DAV is: a framework for architectural validation, gap analysis, and recommendation generation
- What DAV is not: a unit test framework, a documentation generator, a DCM-specific tool
- The consumer model: DAV works on behalf of a consumer project whose content lives in a structured subdirectory of the consumer's repo
- What DAV produces: structured analyses conforming to the Analysis Output Schema (`spec 07`)
- The three operating modes at a high level: verification, reproduce, explore (detailed in `spec 04`)
- Where to go next: pointers to `spec 02` (stages), `spec 05` (use cases), `spec 08` (how to become a consumer)

This document will be authored last in the initial spec set. Writing a framework overview before the specifics are stable produces misleading overviews. The other specs inform what this one should say.
