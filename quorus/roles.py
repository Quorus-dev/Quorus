"""Room roles, shared by the relay (routes/room_auth.py) and the wake daemon.

A member with one of WRITER_ROLES may change a room; an observer may only read
it. Anything else, an unknown role included, counts as not a writer.
"""

OBSERVER = "observer"
WRITER_ROLES = frozenset({"builder", "reviewer", "researcher", "pm", "qa", "member"})
