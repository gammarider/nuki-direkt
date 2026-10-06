"""Bounded transport for setup only; never used for normal lock actions."""
from __future__ import annotations

import asyncio

from bleak import BleakError

from .status_reconnect import StatusReconnectNukiDevice

CLEANUP_TIMEOUT = 5


class PairingNukiDevice(StatusReconnectNukiDevice):
    """Use one transmission per setup message and retain partial credentials."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.send_retry = 1
        self.response_retry = 1
        self.authorization_started = False

    async def _send_command(
        self, characteristic, command, aggregate_messages=None,
        expected_response=None, response_retry=None,
    ):
        # pyNukiBT 0.0.20 treats response_retry=0 as the default. Always pass
        # one attempt explicitly, including for Ultra authorization messages.
        if (
            self._const is not None
            and expected_response == self._const.NukiCommand.AUTHORIZATION_ID
        ):
            self.authorization_started = True
        return await super()._send_command(
            characteristic, command, aggregate_messages=aggregate_messages,
            expected_response=expected_response, response_retry=1,
        )

    def pairing_credentials(self):
        """Return an incomplete candidate for read-only verification, not trust."""
        if self._auth_id is None or self._nuki_public_key is None:
            return None
        if len(self._auth_id) != 4 or len(self._nuki_public_key) != 32:
            return None
        return {"auth_id": self._auth_id, "nuki_public_key": self._nuki_public_key}

    async def disconnect(self):
        """Do not swallow cleanup failures or occupy a proxy slot indefinitely."""
        self._notifications_ready_client = None
        client = self._client
        if client is None:
            return
        async with asyncio.timeout(CLEANUP_TIMEOUT):
            await client.disconnect()
        if client.is_connected:
            raise BleakError("Pairing connection could not be released")
        self._client = None
