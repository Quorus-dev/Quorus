"""Credential redaction: relay access logs and agent-written room posts."""

import logging
import re
from collections.abc import Iterable, Mapping

REDACTED = "[redacted]"

# High-confidence credential shapes only: a chat message about code must not
# get mangled, so generic `KEY=value` text is left alone.
_SECRET_SHAPES = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)"
    r"|\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"
    r"|\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bAIza[0-9A-Za-z_-]{35}"
    r"|\bxox[abposr]-[A-Za-z0-9-]{10,}"
    r"|\b(?:mct|quorus_sk)_[A-Za-z0-9_-]{16,}"
    r"|\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
)
_SECRET_ENV = re.compile(r"(?i)(SECRET|TOKEN|PASSWORD|API_?KEY|PRIVATE_KEY|CREDENTIAL)")


def env_secret_values(env: Mapping[str, str]) -> list[str]:
    """Values of secret-named env vars, long enough to redact without noise."""
    return [v for k, v in env.items() if _SECRET_ENV.search(k) and len(v or "") >= 12]


def redact_secrets(text: str, known: Iterable[str] = ()) -> str:
    """Mask credentials before agent-written text is posted to a room.

    A room message lands in persistent history and in every member's prompt,
    so a prompt-injected agent that prints a key must not publish it.
    *known* are exact secret values this process holds (relay secret, vendor
    keys); they are masked whatever their shape.
    """
    if not text:
        return text
    for value in sorted({v for v in known if v and len(v) >= 12}, key=len, reverse=True):
        text = text.replace(value, REDACTED)
    return _SECRET_SHAPES.sub(REDACTED, text)


def redact_query_tokens(value: str) -> str:
    """Keep request context while removing bearer tokens carried in URLs."""
    return re.sub(r"([?&]token=)[^&\s\"'<>]+", r"\1[REDACTED]", value, flags=re.IGNORECASE)


class QueryTokenFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn's AccessFormatter unpacks the original five arguments;
        # preserve their shape instead of flattening the entire message.
        if isinstance(record.args, tuple):
            record.args = tuple(redact_query_tokens(v) if isinstance(v, str) else v
                                for v in record.args)
        if isinstance(record.msg, str):
            record.msg = redact_query_tokens(record.msg)
        return True


def protect_access_log() -> None:
    """Install before uvicorn configures handlers; logger filters survive it."""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, QueryTokenFilter) for f in logger.filters):
        logger.addFilter(QueryTokenFilter())
