"""Targeted status-read recovery for pyNukiBT 0.0.20."""
from __future__ import annotations

import asyncio
import logging

from bleak import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from pyNukiBT import NukiDevice, NukiConst
from pyNukiBT.const import NukiLockConst, NukiOpenerConst, NukiUltraConst

_LOGGER = logging.getLogger(__name__)


class StatusReconnectNukiDevice(NukiDevice):
    """Wake only a pending state read when its BLE connection disappears."""

    async def lock_action(self, *args, **kwargs):
        """Mark a completed action for a fresh subsequent read connection."""
        result = await super().lock_action(*args, **kwargs)
        if result.status == self._const.StatusCode.COMPLETED:
            self._fresh_connection_for_state = True
        return result

    async def _send_encrypted_command(self, cmd, payload, *args, **kwargs):
        if (
            getattr(self, "_fresh_connection_for_state", False)
            and cmd == self._const.NukiCommand.REQUEST_DATA
            and payload.get("command") in (
                self._const.NukiCommand.KEYTURNER_STATES,
                self._const.NukiCommand.CHALLENGE,
            )
        ):
            # State/log reads and challenges own _operation_lock here, so no
            # motor action can start while the old connection is released.
            await self.disconnect()
            if self._client and self._client.is_connected:
                raise BleakError("Could not release previous Nuki connection")
            self._fresh_connection_for_state = False
            _LOGGER.warning(
                "Nuki %s: fresh BLE connection for post-action state/log request",
                getattr(self, "_address", "unknown"),
            )
        return await super()._send_encrypted_command(cmd, payload, *args, **kwargs)

    def _on_status_disconnect(self, client):
        if client is self._client:
            self._notifications_ready_client = None
        future = self._notify_future
        if (
            client is self._client
            and not client.is_connected
            and self._const is not None
            and self._expected_response == self._const.NukiCommand.KEYTURNER_STATES
            and future is not None
            and not future.done()
        ):
            _LOGGER.warning(
                "Nuki %s: BLE disconnected during status read; using bounded read retry",
                getattr(self, "_address", "unknown"),
            )
            # The upstream response loop handles TimeoutError with at most
            # response_retry attempts. Motor/CHALLENGE waits are untouched.
            future.set_exception(asyncio.TimeoutError())

    async def _discard_incomplete_connection(self):
        self._notifications_ready_client = None
        client = self._client
        if client is None:
            return
        # Keep the client reference if cleanup fails, so the next attempt
        # cannot silently abandon a connected proxy slot or reuse it as ready.
        async with asyncio.timeout(5):
            await client.disconnect()
        if client.is_connected:
            raise BleakError("Could not release incomplete Nuki connection")
        self._client = None

    async def connect(self):
        """Reuse only a connection with both notification channels ready."""
        async with self._connect_lock:
            if not self._ble_device:
                self.set_ble_device()
            if self._client and self._client.is_connected:
                if getattr(self, "_notifications_ready_client", None) is self._client:
                    return
                await self._discard_incomplete_connection()
            self._notifications_ready_client = None
            self._client = await establish_connection(
                BleakClientWithServiceCache,
                self._ble_device,
                f"Nuki {self._address}",
                disconnected_callback=self._on_status_disconnect,
            )
            try:
                if not self._device_type or not self._const:
                    services = self._client.services
                    if services.get_characteristic(NukiOpenerConst.BLE_PAIRING_CHAR):
                        self._device_type = NukiConst.NukiDeviceType.OPENER
                        self._const = NukiOpenerConst
                    elif services.get_characteristic(NukiLockConst.BLE_PAIRING_CHAR):
                        self._device_type = NukiConst.NukiDeviceType.SMARTLOCK_1_2
                        self._const = NukiLockConst
                    elif services.get_characteristic(NukiUltraConst.BLE_PAIRING_CHAR):
                        self._device_type = NukiConst.NukiDeviceType.SMARTLOCK_ULTRA
                        self._const = NukiUltraConst
                    else:
                        raise BleakError("Could not determine Nuki device type")
                # Unlike upstream's helper, do not swallow EOFError here:
                # both subscriptions must succeed before this client is ready.
                await self._client.start_notify(
                    self._const.BLE_PAIRING_CHAR, self._notification_handler
                )
                await self._client.start_notify(
                    self._const.BLE_CHAR, self._notification_handler
                )
                if not self._client.is_connected:
                    raise BleakError("Nuki disconnected during notification setup")
                self._notifications_ready_client = self._client
            except (Exception, asyncio.CancelledError):
                try:
                    await self._discard_incomplete_connection()
                except Exception:
                    _LOGGER.warning("Could not clean up incomplete Nuki connection")
                raise
