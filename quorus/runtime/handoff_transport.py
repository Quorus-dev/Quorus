"""Fail closed when a relay cannot accept capsules and claim moves together."""


class RelayTooOld(RuntimeError):
    def __init__(self):
        super().__init__("relay too old for handoff; upgrade the relay to a version "
                         "supporting atomic capsule handoffs")


class HandoffRejected(RuntimeError):
    """A compatible relay refused this operation, not the protocol itself."""

    def __init__(self, detail):
        # Validation error arrays can echo capsule input. Only show a bounded
        # textual relay reason, never the raw response or submitted payload.
        self.detail = (" ".join(detail.split())[:240] if isinstance(detail, str)
                       else "relay rejected the handoff request")
        super().__init__(self.detail)
