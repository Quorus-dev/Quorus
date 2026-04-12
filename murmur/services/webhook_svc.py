"""Webhook service — registration, SSRF-safe validation, and reliable delivery.

Delivery uses a background worker with retry + exponential backoff + DLQ.
Payloads are signed with HMAC-SHA256 for verification by receivers.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
import structlog
from fastapi import HTTPException

from murmur.backends.protocol import WebhookBackend, WebhookQueueBackend

logger = structlog.get_logger("murmur.services.webhook")

_DEFAULT_CONCURRENCY = 10
_MAX_RETRIES = int(os.environ.get("WEBHOOK_MAX_RETRIES", "3"))
_BACKOFF_BASE = 2  # seconds — retries at 2s, 4s, 8s
_DLQ_MAX_SIZE = 1000
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")


WEBHOOK_REPLAY_WINDOW = int(os.environ.get("WEBHOOK_REPLAY_WINDOW", "300"))  # 5 min


@dataclass
class WebhookJob:
    """A webhook delivery job with retry tracking."""

    target: str
    callback_url: str
    payload: dict
    secret: str = ""  # per-webhook secret, falls back to global
    attempt: int = 0
    created_at: float = field(default_factory=time.time)
    last_error: str = ""


class WebhookService:
    """Manages DM and room webhook registrations and delivers payloads.

    Delivery queue can be either in-memory (asyncio.Queue) or durable
    (Redis Streams via WebhookQueueBackend). When queue_backend is provided,
    jobs survive process restarts and are automatically retried.
    """

    def __init__(
        self,
        backend: WebhookBackend,
        queue_backend: WebhookQueueBackend | None = None,
        concurrency: int = _DEFAULT_CONCURRENCY,
    ) -> None:
        self._backend = backend
        self._queue_backend = queue_backend
        self._semaphore = asyncio.Semaphore(concurrency)
        self._client: httpx.AsyncClient | None = None
        # In-memory fallback queue (used when queue_backend is None)
        self._queue: asyncio.Queue[WebhookJob] = asyncio.Queue(maxsize=10000)
        self._dlq: list[WebhookJob] = []
        self._worker_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        # Delivery stats (in-memory counters; durable backend has its own)
        self._stats = {
            "total_enqueued": 0,
            "total_delivered": 0,
            "total_failed": 0,
            "total_retried": 0,
        }

    def start(self) -> None:
        """Start the background delivery worker."""
        if self._worker_task is None or self._worker_task.done():
            self._stop_event.clear()
            self._worker_task = asyncio.create_task(self._worker_loop())
            logger.info("Webhook delivery worker started")

    async def close(self) -> None:
        """Stop the worker and close the HTTP client."""
        self._stop_event.set()
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # -- HTTP client ----------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=10)
        return self._client

    # -- Payload signing -------------------------------------------------------

    @staticmethod
    def sign_payload(payload: dict, timestamp: int, secret: str = "") -> str:
        """Compute HMAC-SHA256 signature for a webhook payload.

        Signature covers: ``{timestamp}.{canonical_json_body}``
        This prevents replay attacks if the receiver validates the timestamp
        is within an acceptable window (e.g., 5 minutes).
        """
        key = (secret or WEBHOOK_SECRET).encode()
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        signed_data = f"{timestamp}.{body}"
        return hmac.new(key, signed_data.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def verify_signature(
        payload: dict,
        timestamp: int,
        signature: str,
        secret: str = "",
        max_age: int = WEBHOOK_REPLAY_WINDOW,
    ) -> bool:
        """Verify webhook signature and timestamp.

        Returns True if signature is valid and timestamp is within max_age.
        """
        # Check timestamp is recent enough
        now = int(time.time())
        if abs(now - timestamp) > max_age:
            return False
        expected = WebhookService.sign_payload(payload, timestamp, secret)
        return hmac.compare_digest(expected, signature)

    # -- URL validation (SSRF protection) -------------------------------------

    @staticmethod
    def _is_private_ip(
        ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    ) -> bool:
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )

    @classmethod
    def _validate_url_sync(cls, callback_url: str) -> str:
        """Synchronous structural validation of a webhook URL."""
        parsed = urlparse(callback_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise HTTPException(
                status_code=400,
                detail="callback_url must be an http(s) URL",
            )
        if parsed.username or parsed.password:
            raise HTTPException(
                status_code=400,
                detail="callback_url must not include credentials",
            )
        host = parsed.hostname.rstrip(".").lower()
        if (
            host == "localhost"
            or host.endswith(".localhost")
            or host.endswith(".local")
        ):
            raise HTTPException(
                status_code=400,
                detail="callback_url must target a public host",
            )
        try:
            ip = ipaddress.ip_address(host)
            if cls._is_private_ip(ip):
                raise HTTPException(
                    status_code=400,
                    detail="callback_url must target a public host",
                )
            return callback_url
        except ValueError:
            pass
        if "." not in host:
            raise HTTPException(
                status_code=400,
                detail="callback_url must target a public host",
            )
        return callback_url

    @classmethod
    async def validate_url(cls, callback_url: str) -> str:
        """Full async validation including DNS resolution check."""
        url = cls._validate_url_sync(callback_url)
        parsed = urlparse(url)
        host = parsed.hostname.rstrip(".").lower()
        try:
            ipaddress.ip_address(host)
            return url
        except ValueError:
            pass
        try:
            addrinfo = await asyncio.to_thread(
                socket.getaddrinfo, host, None, 0, socket.SOCK_STREAM
            )
        except socket.gaierror:
            raise HTTPException(
                status_code=400,
                detail="callback_url hostname could not be resolved",
            )
        if not addrinfo:
            raise HTTPException(
                status_code=400,
                detail="callback_url hostname could not be resolved",
            )
        for _family, _, _, _, sockaddr in addrinfo:
            resolved_ip = ipaddress.ip_address(sockaddr[0])
            if cls._is_private_ip(resolved_ip):
                raise HTTPException(
                    status_code=400,
                    detail="callback_url must not resolve to a private address",
                )
        return url

    # -- DM webhooks ----------------------------------------------------------

    async def register_dm(
        self, tenant_id: str, name: str, url: str
    ) -> None:
        validated = await self.validate_url(url)
        await self._backend.register_dm(tenant_id, name, validated)

    async def get_dm_url(
        self, tenant_id: str, name: str
    ) -> str | None:
        return await self._backend.get_dm(tenant_id, name)

    async def delete_dm(self, tenant_id: str, name: str) -> None:
        await self._backend.delete_dm(tenant_id, name)

    # -- Room webhooks --------------------------------------------------------

    async def register_room(
        self,
        tenant_id: str,
        room_id: str,
        url: str,
        registered_by: str,
    ) -> None:
        validated = await self.validate_url(url)
        await self._backend.register_room(
            tenant_id, room_id, validated, registered_by
        )

    async def list_room(
        self, tenant_id: str, room_id: str
    ) -> list[dict]:
        return await self._backend.list_room(tenant_id, room_id)

    async def delete_room(
        self, tenant_id: str, room_id: str, url: str
    ) -> bool:
        return await self._backend.delete_room(tenant_id, room_id, url)

    # -- Delivery queue -------------------------------------------------------

    async def _enqueue_job_async(
        self, target: str, url: str, payload: dict, secret: str = ""
    ) -> None:
        """Add a delivery job to the durable queue backend."""
        job_data = {
            "target": target,
            "callback_url": url,
            "payload": payload,
            "secret": secret,
            "attempt": 0,
            "created_at": time.time(),
            "last_error": "",
        }
        await self._queue_backend.enqueue(job_data)
        self._stats["total_enqueued"] += 1

    def _enqueue_job(
        self, target: str, url: str, payload: dict, secret: str = ""
    ) -> None:
        """Add a delivery job to the in-memory queue (non-blocking, drops if full)."""
        job = WebhookJob(
            target=target, callback_url=url, payload=payload, secret=secret
        )
        try:
            self._queue.put_nowait(job)
            self._stats["total_enqueued"] += 1
        except asyncio.QueueFull:
            logger.warning("Webhook queue full, dropping job for %s", target)
            self._stats["total_failed"] += 1

    async def notify_dm(
        self, tenant_id: str, recipient: str, message: dict
    ) -> None:
        """Queue DM webhook delivery if registered."""
        hook = await self._backend.get_dm(tenant_id, recipient)
        if not hook:
            return
        if self._queue_backend:
            await self._enqueue_job_async(
                recipient, hook["url"], message, hook.get("secret", "")
            )
        else:
            self._enqueue_job(recipient, hook["url"], message, hook.get("secret", ""))

    async def notify_room(
        self, tenant_id: str, room_id: str, message: dict
    ) -> None:
        """Queue room webhook deliveries for all registered URLs."""
        hooks = await self._backend.list_room(tenant_id, room_id)
        for hook in hooks:
            if self._queue_backend:
                await self._enqueue_job_async(
                    f"room:{room_id}", hook["url"], message, hook.get("secret", "")
                )
            else:
                self._enqueue_job(
                    f"room:{room_id}", hook["url"], message, hook.get("secret", "")
                )

    # -- Background worker ----------------------------------------------------

    async def _worker_loop(self) -> None:
        """Process webhook jobs with retry and exponential backoff."""
        if self._queue_backend:
            await self._worker_loop_durable()
        else:
            await self._worker_loop_memory()

    async def _worker_loop_memory(self) -> None:
        """Worker loop for in-memory queue."""
        while not self._stop_event.is_set():
            try:
                job = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            await self._process_job(job)

    async def _worker_loop_durable(self) -> None:
        """Worker loop for durable Redis Streams queue."""
        while not self._stop_event.is_set():
            try:
                jobs = await self._queue_backend.fetch(count=10)
            except Exception as e:
                logger.warning("Failed to fetch webhook jobs: %s", e)
                await asyncio.sleep(1.0)
                continue

            if not jobs:
                await asyncio.sleep(1.0)
                continue

            for job_id, job_data in jobs:
                await self._process_job_durable(job_id, job_data)

    async def _process_job(self, job: WebhookJob) -> None:
        """Attempt delivery with retry on failure (in-memory queue)."""
        async with self._semaphore:
            try:
                client = self._get_client()
                timestamp = int(time.time())
                headers: dict[str, str] = {
                    "Content-Type": "application/json",
                    "X-Webhook-Attempt": str(job.attempt + 1),
                }
                secret = job.secret or WEBHOOK_SECRET
                if secret:
                    sig = self.sign_payload(job.payload, timestamp, secret)
                    headers["X-Murmur-Timestamp"] = str(timestamp)
                    headers["X-Murmur-Signature"] = f"sha256={sig}"

                resp = await client.post(
                    job.callback_url, json=job.payload, headers=headers
                )
                resp.raise_for_status()
                self._stats["total_delivered"] += 1
                logger.info(
                    "Webhook delivered to %s for %s (attempt %d)",
                    job.callback_url,
                    job.target,
                    job.attempt + 1,
                )
            except Exception as exc:
                job.attempt += 1
                job.last_error = str(exc)

                if job.attempt < _MAX_RETRIES:
                    # Retry with exponential backoff
                    delay = _BACKOFF_BASE ** job.attempt
                    self._stats["total_retried"] += 1
                    logger.warning(
                        "Webhook delivery failed for %s (attempt %d/%d), "
                        "retrying in %ds: %s",
                        job.target,
                        job.attempt,
                        _MAX_RETRIES,
                        delay,
                        exc,
                    )
                    asyncio.get_running_loop().call_later(
                        delay, self._requeue_job, job
                    )
                else:
                    # Dead letter — max retries exhausted
                    self._stats["total_failed"] += 1
                    if len(self._dlq) < _DLQ_MAX_SIZE:
                        self._dlq.append(job)
                    logger.error(
                        "Webhook delivery permanently failed for %s at %s "
                        "after %d attempts: %s",
                        job.target,
                        job.callback_url,
                        job.attempt,
                        job.last_error,
                    )

    async def _process_job_durable(self, job_id: str, job_data: dict) -> None:
        """Attempt delivery with ACK/NACK (durable queue)."""
        async with self._semaphore:
            target = job_data.get("target", "unknown")
            callback_url = job_data.get("callback_url", "")
            payload = job_data.get("payload", {})
            secret = job_data.get("secret", "")
            attempt = job_data.get("attempt", 0)

            try:
                client = self._get_client()
                timestamp = int(time.time())
                headers: dict[str, str] = {
                    "Content-Type": "application/json",
                    "X-Webhook-Attempt": str(attempt + 1),
                }
                effective_secret = secret or WEBHOOK_SECRET
                if effective_secret:
                    sig = self.sign_payload(payload, timestamp, effective_secret)
                    headers["X-Murmur-Timestamp"] = str(timestamp)
                    headers["X-Murmur-Signature"] = f"sha256={sig}"

                resp = await client.post(
                    callback_url, json=payload, headers=headers
                )
                resp.raise_for_status()
                self._stats["total_delivered"] += 1
                logger.info(
                    "Webhook delivered to %s for %s (attempt %d)",
                    callback_url,
                    target,
                    attempt + 1,
                )
                # Success — ACK the job
                await self._queue_backend.ack(job_id)

            except Exception as exc:
                error_msg = str(exc)
                logger.warning(
                    "Webhook delivery failed for %s (attempt %d/%d): %s",
                    target,
                    attempt + 1,
                    _MAX_RETRIES,
                    exc,
                )
                # NACK the job — backend handles retry or DLQ
                will_retry = await self._queue_backend.nack(
                    job_id, error_msg, _MAX_RETRIES
                )
                if will_retry:
                    self._stats["total_retried"] += 1
                else:
                    self._stats["total_failed"] += 1
                    logger.error(
                        "Webhook delivery permanently failed for %s at %s "
                        "after %d attempts: %s",
                        target,
                        callback_url,
                        attempt + 1,
                        error_msg,
                    )

    def _requeue_job(self, job: WebhookJob) -> None:
        """Re-add a job to the queue after backoff delay."""
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            self._stats["total_failed"] += 1
            logger.warning(
                "Webhook queue full during retry, dropping job for %s",
                job.target,
            )

    # -- Monitoring -----------------------------------------------------------

    async def get_stats_async(self) -> dict:
        """Return delivery statistics (async, supports durable backend)."""
        if self._queue_backend:
            backend_stats = await self._queue_backend.get_stats()
            return {
                **self._stats,
                "queue_size": backend_stats.get("queue_length", 0),
                "pending": backend_stats.get("pending", 0),
                "dlq_size": backend_stats.get("dlq_size", 0),
                "durable": True,
            }
        return {
            **self._stats,
            "queue_size": self._queue.qsize(),
            "dlq_size": len(self._dlq),
            "durable": False,
        }

    def get_stats(self) -> dict:
        """Return delivery statistics (sync, in-memory only)."""
        return {
            **self._stats,
            "queue_size": self._queue.qsize(),
            "dlq_size": len(self._dlq),
            "durable": self._queue_backend is not None,
        }

    async def get_dlq_async(self, limit: int = 50) -> list[dict]:
        """Return recent DLQ entries (async, supports durable backend)."""
        if self._queue_backend:
            return await self._queue_backend.get_dlq(limit)
        return self.get_dlq(limit)

    def get_dlq(self, limit: int = 50) -> list[dict]:
        """Return recent DLQ entries for monitoring (in-memory only)."""
        return [
            {
                "target": j.target,
                "url": j.callback_url,
                "attempts": j.attempt,
                "last_error": j.last_error,
                "created_at": j.created_at,
            }
            for j in self._dlq[-limit:]
        ]
