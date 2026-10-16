"""Credential-safe relay access logging."""

import logging
import re


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
