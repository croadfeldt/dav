# Output bindings

A resource may publish outputs (an endpoint, a generated identifier). An intent may
bind another resource's input to a published output by naming the source resource
and the output key.

## Declaration requirement  (SPECIFIED)

A binding may only name a source that the same intent declares, or an existing
resource in the caller's tenant that the caller may read. A binding naming an
undeclared, unreadable, or non-existent source is refused at admission with
`BINDING_UNDECLARED`. The `remediation` field names the declaration the intent is
missing.

Validation happens before any resource is realized.
