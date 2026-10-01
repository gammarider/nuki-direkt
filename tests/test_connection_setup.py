"""Connection readiness regression tests, with no real BLE or HA calls."""
import asyncio
import types
import unittest
from unittest.mock import AsyncMock, patch

from test_status_reconnect import Fix, ns
from test_upstream import BleakError


class Tests(unittest.IsolatedAsyncioTestCase):
    def device(self):
        d = Fix.__new__(Fix)
        d._connect_lock = asyncio.Lock()
        d._ble_device = object()
        d._address = 'synthetic'
        d._client = None
        d._device_type = 'known'
        d._const = types.SimpleNamespace(
            BLE_PAIRING_CHAR='pair', BLE_CHAR='data',
            NukiCommand=types.SimpleNamespace(KEYTURNER_STATES='state'))
        d._notification_handler = object()
        d._notify_future = None
        d._expected_response = None
        return d

    def client(self):
        c = types.SimpleNamespace(is_connected=True, start_notify=AsyncMock())
        async def disconnect():
            c.is_connected = False
        c.disconnect = AsyncMock(side_effect=disconnect)
        return c

    async def test_failed_first_or_second_subscription_reconnects(self):
        for failure in (BleakError, EOFError, asyncio.CancelledError):
            for step in (0, 1):
                with self.subTest(failure=failure, step=step):
                    d, old, fresh = self.device(), self.client(), self.client()
                    old.start_notify.side_effect = [None] * step + [failure()]
                    establish = AsyncMock(side_effect=[old, fresh])
                    with patch.dict(ns, establish_connection=establish):
                        with self.assertRaises(failure):
                            await d.connect()
                        old.disconnect.assert_awaited_once()
                        self.assertIsNone(d._client)
                        self.assertIsNone(d._notifications_ready_client)
                        await d.connect()
                        self.assertEqual(fresh.start_notify.await_count, 2)
                        self.assertIs(d._notifications_ready_client, fresh)
                        self.assertEqual(establish.await_count, 2)

    async def test_cleanup_failure_never_reuses_incomplete_client(self):
        d, old, fresh = self.device(), self.client(), self.client()
        old.start_notify.side_effect = BleakError('setup')
        old.disconnect.side_effect = BleakError('cleanup')
        establish = AsyncMock(side_effect=[old, fresh])
        with patch.dict(ns, establish_connection=establish):
            with self.assertRaisesRegex(BleakError, 'setup'):
                await d.connect()
            with self.assertRaisesRegex(BleakError, 'cleanup'):
                await d.connect()
            self.assertIs(d._client, old)
            self.assertIsNone(d._notifications_ready_client)
            self.assertEqual(establish.await_count, 1)
            async def recover():
                old.is_connected = False
            old.disconnect.side_effect = recover
            await d.connect()
            self.assertIs(d._notifications_ready_client, fresh)

    async def test_disconnect_that_leaves_link_up_blocks_reuse(self):
        d, c = self.device(), self.client()
        d._client = c
        c.disconnect.side_effect = None
        with self.assertRaisesRegex(BleakError, 'incomplete'):
            await d.connect()
        c.start_notify.assert_not_awaited()

    async def test_link_drop_during_setup_not_ready(self):
        d, c = self.device(), self.client()
        async def drop(*args):
            c.is_connected = False
        c.start_notify.side_effect = drop
        with patch.dict(ns, establish_connection=AsyncMock(return_value=c)):
            with self.assertRaisesRegex(BleakError, 'disconnected during'):
                await d.connect()
        self.assertIsNone(d._notifications_ready_client)
        c.disconnect.assert_awaited_once()

    async def test_concurrent_connects_only_initialize_once(self):
        d, c = self.device(), self.client()
        establish = AsyncMock(return_value=c)
        with patch.dict(ns, establish_connection=establish):
            await asyncio.gather(d.connect(), d.connect())
        self.assertEqual(establish.await_count, 1)
        self.assertEqual(c.start_notify.await_count, 2)

    async def test_disconnect_invalidates_only_current_client(self):
        d, c = self.device(), self.client()
        d._client = d._notifications_ready_client = c
        d._on_status_disconnect(self.client())
        self.assertIs(d._notifications_ready_client, c)
        c.is_connected = False
        d._on_status_disconnect(c)
        self.assertIsNone(d._notifications_ready_client)

    async def test_establish_failure_propagates_without_ready_client(self):
        d = self.device()
        with patch.dict(ns, establish_connection=AsyncMock(side_effect=BleakError('connect'))):
            with self.assertRaisesRegex(BleakError, 'connect'):
                await d.connect()
        self.assertIsNone(d._notifications_ready_client)

    async def test_cleanup_timeout_retains_unready_client(self):
        d, c = self.device(), self.client()
        d._client = c
        original_timeout = asyncio.timeout
        async def hang():
            await asyncio.sleep(10)
        c.disconnect.side_effect = hang
        with patch.object(asyncio, 'timeout', side_effect=lambda _: original_timeout(.01)):
            with self.assertRaises(TimeoutError):
                await d._discard_incomplete_connection()
        self.assertIs(d._client, c)
        self.assertIsNone(d._notifications_ready_client)
