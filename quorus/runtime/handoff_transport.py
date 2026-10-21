"""Fail closed when a relay cannot accept capsules and claim moves together."""


class RelayTooOld(RuntimeError):
    def __init__(self):
        super().__init__("relay too old for handoff; upgrade the relay to a version "
                         "supporting atomic capsule handoffs")
