"""Async client for the StreamUnlimited StreamSDK "nSDK" HTTP API.

Reverse-engineered from /jsapi/nsdk-api.js on a Fosi Audio S3
(board FosiS3Stream1832, StreamSDK 1.0.262).

    GET  /api/getData?path=&roles=       -> list, one entry per role
    POST /api/setData  {path, role, value}
    GET  /api/getRows?path=&roles=&from=&to=
    POST /api/event/modifyQueue
    GET  /api/event/pollQueue?queueId=&timeout=

Values are tagged unions: {"type": "i32_", "i32_": 3}
Application errors come back as HTTP 500 with {"error": {"name", "message"}}.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from urllib.parse import quote

import aiohttp

_LOGGER = logging.getLogger(__name__)

# Identity nodes, in preference order per field. Only settings:/deviceName and
# settings:/system/productName are confirmed on hardware; the rest are
# best-effort and every one of them is allowed to be absent. Node names differ
# between StreamSDK builds, so nothing here may be treated as required.
IDENTITY_PATHS: dict[str, tuple[str, ...]] = {
    "model": ("settings:/system/productName", "settings:/system/modelName"),
    "manufacturer": ("settings:/system/manufacturer",),
    "name": ("settings:/deviceName",),
    "serial": ("settings:/system/serialNumber",),
    "sw_version": ("settings:/system/versionNumber", "settings:/version"),
    "mac": ("settings:/system/primaryMacAddress",),
}

# Ceiling for the whole identity probe. Each miss costs a round trip, and the
# device is regularly slow to answer on wifi, so bound the total rather than
# letting setup stall for timeout * len(IDENTITY_PATHS).
VERIFY_TIMEOUT = 20.0


class NsdkError(Exception):
    """Base error."""


class NsdkConnectionError(NsdkError):
    """Device unreachable. Expected regularly if it is on flaky wifi."""


class NsdkPathError(NsdkError):
    """Node does not exist, or is not readable by that role."""


class NsdkUnsupportedError(NsdkError):
    """Answered HTTP, but exposes no node we recognise.

    Distinct from NsdkError because this one is permanent - retrying will not
    turn a non-StreamSDK box into a StreamSDK box.
    """


def encode(value: Any) -> dict[str, Any]:
    """Python value -> tagged union."""
    if isinstance(value, dict):
        return value
    if isinstance(value, bool):
        return {"type": "bool_", "bool_": value}
    if isinstance(value, int):
        return {"type": "i32_", "i32_": value}
    if isinstance(value, float):
        return {"type": "double_", "double_": value}
    return {"type": "string_", "string_": str(value)}


def decode(value: Any) -> Any:
    """Tagged union -> Python value."""
    if not isinstance(value, dict):
        return value
    kind = value.get("type")
    if kind and kind in value:
        return value[kind]
    return value


class NsdkClient:
    """Thin async wrapper over the nSDK endpoints."""

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        timeout: float = 6.0,
    ) -> None:
        self._host = host
        self._base = f"http://{host}/api"
        self._session = session
        self._timeout = timeout

    @property
    def host(self) -> str:
        return self._host

    @staticmethod
    def _error_for(status: int, body: str) -> NsdkError:
        """Map an HTTP failure onto the right exception.

        nSDK signals application-layer failures with HTTP 500 and a JSON body
        of {"error": {"name", "message"}}, so a 500 is not necessarily a fault
        - an invalidPath 500 is the documented answer for "no such node".
        """
        message = body[:200]
        try:
            error = json.loads(body).get("error", {})
            message = f"{error.get('name')}: {error.get('message')}"
        except (ValueError, AttributeError):
            pass  # body was not the JSON envelope; keep the raw excerpt
        if "invalidPath" in message or "does not exist" in message:
            return NsdkPathError(message)
        return NsdkError(f"HTTP {status}: {message}")

    async def _request(
        self, url: str, payload: dict[str, Any] | None = None
    ) -> Any:
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        try:
            if payload is None:
                resp = await self._session.get(url, timeout=timeout)
            else:
                resp = await self._session.post(url, json=payload, timeout=timeout)

            async with resp:
                body = await resp.text()

                if resp.status >= 400:
                    raise self._error_for(resp.status, body)

                if not body.strip():
                    return None
                try:
                    return json.loads(body)
                except ValueError as exc:
                    raise NsdkError(f"non-JSON reply: {body[:200]}") from exc

        except (aiohttp.ClientError, TimeoutError) as exc:
            raise NsdkConnectionError(f"{self._host} unreachable: {exc}") from exc

    def _url(self, endpoint: str, params: dict[str, str]) -> str:
        params = dict(params)
        # The web UI appends this to defeat caching; harmless and matches it.
        params["_nocache"] = str(int(time.time() * 1000))
        # encodeURIComponent semantics, as nsdk-api.js uses: node paths carry
        # ':' and '/' and the device's parser expects them percent-encoded.
        # yarl preserves existing valid %XX sequences, so this is not
        # double-encoded when aiohttp parses the URL.
        query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
        return f"{self._base}{endpoint}?{query}"

    # ---------------------------------------------------------------- API

    async def get_data(self, path: str, roles: str = "value") -> Any:
        return await self._request(
            self._url("/getData", {"path": path, "roles": roles})
        )

    async def set_data(self, path: str, value: Any, role: str = "value") -> Any:
        return await self._request(
            f"{self._base}/setData", {"path": path, "role": role, "value": value}
        )

    async def get_rows(
        self,
        path: str,
        roles: str = "path,type,value,title",
        frm: int = 0,
        to: int = 200,
    ) -> Any:
        return await self._request(
            self._url(
                "/getRows",
                {"path": path, "roles": roles, "from": str(frm), "to": str(to)},
            )
        )

    # ------------------------------------------------------------ helpers

    async def read(self, path: str) -> Any:
        """Read the `value` role of a node."""
        data = await self.get_data(path, "value")
        if isinstance(data, list) and data:
            return decode(data[0])
        return decode(data)

    async def write(self, path: str, value: Any) -> Any:
        """Write the `value` role of a node."""
        return await self.set_data(path, encode(value), "value")

    async def activate(self, path: str, value: Any = None) -> Any:
        """Invoke a node - setData with the `activate` role.

        An action node normally takes no argument, and the device wants an
        empty object for that case rather than a tagged union. Anything else
        must be encoded like any other value.
        """
        payload = encode(value) if value is not None else {}
        return await self.set_data(path, payload, "activate")

    async def _read_first(self, paths: tuple[str, ...]) -> tuple[Any, bool]:
        """Read the first of `paths` that exists.

        Returns (value, existed). `existed` distinguishes "the node is there
        and happens to be empty" from "no such node", which matters because
        the second is what tells us this is not a StreamSDK device.
        """
        for path in paths:
            try:
                return await self.read(path), True
            except NsdkPathError:
                continue
        return None, False

    async def async_verify(self) -> dict[str, Any]:
        """Collect device identity, and confirm this really is StreamSDK.

        getData raises invalidPath for a node that does not exist and returns
        quietly for one that does, which makes it an existence oracle. So the
        test is not "did we get a model name" - firmware builds disagree about
        node names - but "did *any* known node resolve at all".
        """
        info: dict[str, Any] = {}
        found = False
        try:
            async with asyncio.timeout(VERIFY_TIMEOUT):
                for key, paths in IDENTITY_PATHS.items():
                    value, exists = await self._read_first(paths)
                    info[key] = value
                    found = found or exists
        except TimeoutError as exc:
            raise NsdkConnectionError(
                f"{self._host} did not finish the identity probe in "
                f"{VERIFY_TIMEOUT:.0f}s"
            ) from exc

        if not found:
            raise NsdkUnsupportedError(
                "responded, but exposes no known StreamSDK node"
            )
        _LOGGER.debug("Identified %s as %r", self._host, info)
        return info
