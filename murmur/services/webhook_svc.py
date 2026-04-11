"""Webhook service — registration, SSRF-safe validation, and async delivery."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpx
import structlog
from fastapi import HTTPException

from murmur.backends.protocol import WebhookBackend

logger = structlog.get_logger("murmur.services.webhook")

_DEFAULT_CONCURRENCY = 10


class WebhookService:
    """Manages DM and room webhook registrations and delivers payloads."""

    def __init__(
        self,
        backend: WebhookBackend,
        concurrency: int = _DEFAULT_CONCURRENCY,
    ) -> None:
        self._backend = backend
        self._semaphore = asyncio.Semaphore(concurrency)
        self._client: httpx.AsyncClient | None = None

    # -- HTTP client ----------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=5)
        return self._client

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
        # If it's already an IP literal we validated above, skip DNS.
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

    # -- Delivery -------------------------------------------------------------

    async def _deliver(
        self, target: str, callback_url: str, message: dict
    ) -> None:
        async with self._semaphore:
            try:
                client = self._get_client()
                resp = await client.post(callback_url, json=message)
                resp.raise_for_status()
                logger.info(
                    "Webhook delivered to %s for %s", callback_url, target
                )
            except Exception:
                logger.warning(
                    "Webhook delivery failed for %s at %s",
                    target,
                    callback_url,
                    exc_info=True,
                )

    async def notify_dm(
        self, tenant_id: str, recipient: str, message: dict
    ) -> None:
        """Fire-and-forget DM webhook if registered."""
        url = await self._backend.get_dm(tenant_id, recipient)
        if not url:
            return
        asyncio.create_task(self._deliver(recipient, url, message))

    async def notify_room(
        self, tenant_id: str, room_id: str, message: dict
    ) -> None:
        """Fire-and-forget room webhooks for all registered URLs."""
        hooks = await self._backend.list_room(tenant_id, room_id)
        for hook in hooks:
            asyncio.create_task(
                self._deliver(f"room:{room_id}", hook["url"], message)
            )
