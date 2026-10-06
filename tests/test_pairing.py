"""Setup tests using real HA flows and simulated BLE; never touch a lock."""
import asyncio
import tempfile
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from bleak import BleakError
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_NAME, CONF_PIN
from nacl.public import PrivateKey
from pyNukiBT import NukiConst, NukiErrorException
from pyNukiBT.const import NukiLockConst

from custom_components.hass_nuki_bt import config_flow as flow_module
from custom_components.hass_nuki_bt.config_flow import NukiFlowHandler, normalize_pin, validate_credentials
from custom_components.hass_nuki_bt.const import (
    DOMAIN, CONF_DEVICE_ADDRESS, CONF_CLIENT_TYPE,
    CONF_AUTH_ID, CONF_PRIVATE_KEY, CONF_PUBLIC_KEY, CONF_DEVICE_PUBLIC_KEY, CONF_APP_ID,
)
from custom_components.hass_nuki_bt.pairing import PairingNukiDevice

ADDRESS = 'AA:BB:CC:DD:EE:FF'
INPUT = {CONF_NAME: 'Test lock', CONF_DEVICE_ADDRESS: ADDRESS, CONF_CLIENT_TYPE: 'App'}
RESULT = {'auth_id': b'\x01' * 4, 'nuki_public_key': b'\x02' * 32}


class PairingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.tmp.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.hass.config_entries._initialized.set()
        self.manager = self.hass.config_entries.flow
        self.device = SimpleNamespace(
            connect=AsyncMock(), disconnect=AsyncMock(), pair=AsyncMock(return_value=RESULT),
            update_state=AsyncMock(), _last_update_state_successful=True,
            authorization_started=False, pairing_credentials=lambda: RESULT,
            _device_type=NukiConst.NukiDeviceType.SMARTLOCK_3_4,
        )
        self.patches = [
            patch('homeassistant.config_entries._async_get_flow_handler', AsyncMock(return_value=NukiFlowHandler)),
            patch.object(flow_module.bluetooth, 'async_ble_device_from_address', return_value=object()),
            patch.object(flow_module, 'PairingNukiDevice', return_value=self.device),
        ]
        for p in self.patches:
            p.start()
        self.flow = NukiFlowHandler()
        self.flow.hass = self.hass
        self.flow.handler = DOMAIN
        self.flow.flow_id = 'test-flow'
        self.flow.context = {'source': 'user'}
        self.flow._data = dict(INPUT)

    async def asyncTearDown(self):
        for progress in list(self.manager.async_progress()):
            self.manager.async_abort(progress['flow_id'])
        if self.flow._task and not self.flow._task.done():
            self.flow._task.cancel()
            await asyncio.gather(self.flow._task, return_exceptions=True)
        await asyncio.sleep(0)
        for p in reversed(self.patches):
            p.stop()
        await self.hass.async_stop(force=True)
        self.tmp.cleanup()

    async def finish(self):
        await self.flow._task
        result = await self.flow.async_step_pairing()
        self.assertEqual(result['type'], 'progress_done')
        return await self.flow.async_step_pair_result()

    async def test_success_requires_confirmation_and_creates_valid_entry(self):
        form = await self.flow.async_step_pair()
        self.assertEqual(form['type'], 'form')
        self.device.pair.assert_not_called()
        progress = await self.flow.async_step_pair({CONF_PIN: ''})
        self.assertEqual(progress['type'], 'progress')
        result = await self.finish()
        self.assertEqual(result['type'], 'create_entry')
        self.assertEqual(result['data'][CONF_AUTH_ID], RESULT['auth_id'].hex())
        self.assertNotIn(CONF_PIN, result['data'])
        self.assertEqual(self.flow.unique_id, 'aabbccddeeff')
        self.assertFalse(validate_credentials(result['data'])[1])
        self.device.disconnect.assert_awaited_once()
        self.device.pair.assert_awaited_once_with(None)

    async def test_connect_error_is_form_error_and_cleans_up(self):
        self.device.connect.side_effect = BleakError('synthetic')
        await self.flow.async_step_pair({})
        result = await self.finish()
        self.assertEqual(result['errors'], {'base': 'connection'})
        self.device.disconnect.assert_awaited_once()
        self.device.pair.assert_not_called()

    async def test_unreachable_device_does_not_attempt_connection(self):
        with patch.object(flow_module.bluetooth, 'async_ble_device_from_address', return_value=None):
            await self.flow.async_step_pair({})
            result = await self.finish()
        self.assertEqual(result['errors'], {'base': 'not_found'})
        self.device.connect.assert_not_called()

    async def test_protocol_errors_return_forms(self):
        for code, error in [('P_ERROR_NOT_PAIRING','pairing'), ('P_ERROR_BAD_AUTHENTICATOR','pairing_failed'), ('K_ERROR_BAD_PIN','invalid_pin'), ('K_ERROR_TOO_MANY_PIN_ATTEMPTS','pin_locked'), ('P_ERROR_MAX_USER','authorization_full')]:
            with self.subTest(code=code):
                self.device.pair.side_effect = NukiErrorException(code, None)
                await self.flow.async_step_pair({CONF_PIN: "42"})
                result = await self.finish()
                self.assertEqual(result['errors'], {'base': error})
        self.assertEqual(self.device.disconnect.await_count, 5)

    async def test_explicit_pin_rejection_can_be_corrected_without_lost_id(self):
        self.device.authorization_started = True
        self.device.pairing_credentials = lambda: None
        self.device.pair.side_effect = NukiErrorException('K_ERROR_BAD_PIN', None)
        await self.flow.async_step_pair({CONF_PIN:'42'})
        result = await self.finish()
        self.assertEqual(result['errors'], {'base':'invalid_pin'})
        self.assertFalse(self.flow._uncertain)
        self.assertNotIn(CONF_PIN, self.flow._data)
        result = await self.flow.async_step_pair({})
        self.assertEqual(result['errors'], {CONF_PIN:'pin_required'})
        self.device.pair.side_effect = None
        await self.flow.async_step_pair({CONF_PIN:'43'})
        self.assertEqual((await self.finish())['type'], 'create_entry')

    async def test_unexpected_error_does_not_log_payload(self):
        self.device.pair.side_effect = ValueError('synthetic-secret-marker')
        with self.assertLogs(flow_module.LOGGER, level='WARNING') as logs:
            await self.flow.async_step_pair({})
            result = await self.finish()
        self.assertEqual(result['errors'], {'base':'unknown'})
        self.assertNotIn('synthetic-secret-marker', ''.join(logs.output))

    async def test_pair_timeout_cleanup_and_identity_reuse(self):
        async def wait(*args):
            await asyncio.Event().wait()
        self.device.pair.side_effect = wait
        with patch.object(flow_module, 'PAIR_TIMEOUT', .01):
            await self.flow.async_step_pair({})
            result = await self.finish()
        identity = dict(self.flow._identity)
        self.assertEqual(result['errors'], {'base':'timeout'})
        self.device.disconnect.assert_awaited_once()
        self.device.pair.side_effect = None
        await self.flow.async_step_pair({})
        await self.finish()
        self.assertEqual(identity, self.flow._identity)

    async def test_connect_timeout(self):
        async def wait():
            await asyncio.Event().wait()
        self.device.connect.side_effect = wait
        with patch.object(flow_module, 'CONNECT_TIMEOUT', .01):
            await self.flow.async_step_pair({})
            result = await self.finish()
        self.assertEqual(result['errors'], {'base':'timeout'})
        self.device.disconnect.assert_awaited_once()

    async def test_ultra_requests_pin_before_pairing(self):
        self.device._device_type = NukiConst.NukiDeviceType.SMARTLOCK_ULTRA
        await self.flow.async_step_pair({})
        result = await self.finish()
        self.assertEqual(result['errors'], {'base': 'pin_required'})
        self.device.pair.assert_not_called()
        result = await self.flow.async_step_pair({CONF_PIN: 'abc'})
        self.assertEqual(result['errors'], {CONF_PIN: 'invalid_pin'})
        await self.flow.async_step_pair({CONF_PIN: '00042'})
        result = await self.finish()
        self.device.pair.assert_awaited_once_with(42)
        self.assertEqual(result['data'][CONF_PIN], '00042')

    async def test_cleanup_failure_prevents_another_pair(self):
        self.device.connect.side_effect = BleakError('synthetic')
        self.device.disconnect.side_effect = BleakError('synthetic')
        await self.flow.async_step_pair({})
        result = await self.finish()
        self.assertEqual(result['reason'], 'cleanup_failed')
        self.device.pair.assert_not_called()

    async def test_late_failure_recovers_by_read_only_verification(self):
        self.device.authorization_started = True
        self.device.pair.side_effect = TimeoutError
        await self.flow.async_step_pair({})
        result = await self.finish()
        self.assertEqual(result['step_id'], 'recover')
        await self.flow.async_step_recover({})
        result = await self.finish()
        self.assertEqual(result['type'], 'create_entry')
        self.device.pair.assert_awaited_once()
        self.device.update_state.assert_awaited_once()

    async def test_failed_read_only_verification_does_not_repair(self):
        self.device.authorization_started = True
        self.device.pair.side_effect = TimeoutError
        await self.flow.async_step_pair({})
        await self.finish()
        self.device._last_update_state_successful = False
        await self.flow.async_step_recover({})
        result = await self.finish()
        self.assertEqual(result['step_id'], 'recover')
        self.assertEqual(result['errors'], {'base':'connection'})
        self.device.pair.assert_awaited_once()

    async def test_missing_late_credentials_aborts_without_retry(self):
        self.device.authorization_started = True
        self.device.pairing_credentials = lambda: None
        self.device.pair.side_effect = TimeoutError
        await self.flow.async_step_pair({})
        result = await self.finish()
        self.assertEqual(result['reason'], 'pairing_uncertain')
        self.device.pair.assert_awaited_once()

    async def test_existing_legacy_entry_blocks_normalized_address(self):
        entry = ConfigEntry(domain=DOMAIN, data=INPUT, title='existing', version=1,
            minor_version=1, source='user', unique_id=None, options={},
            discovery_keys=MappingProxyType({}), subentries_data=[])
        self.hass.config_entries._entries[entry.entry_id] = entry
        result = await self.flow.async_step_user({**INPUT, CONF_DEVICE_ADDRESS:'  aa:bb:cc:dd:ee:ff  '})
        self.assertEqual(result['reason'], 'already_configured')
        self.device.connect.assert_not_called()

    async def test_invalid_address_and_pin_remain_in_form(self):
        result = await self.flow.async_step_user({**INPUT, CONF_DEVICE_ADDRESS:'bad', CONF_PIN:'not-a-pin'})
        self.assertEqual(result['errors'], {CONF_DEVICE_ADDRESS:'invalid_address', CONF_PIN:'invalid_pin'})
        self.device.connect.assert_not_called()

    async def test_manual_import_validates_before_creating(self):
        result = await self.flow.async_step_manual({})
        self.assertEqual(len(result['errors']), 5)
        key = PrivateKey.generate()
        data = {CONF_PRIVATE_KEY: bytes(key).hex(), CONF_PUBLIC_KEY:bytes(key.public_key).hex(),
            CONF_DEVICE_PUBLIC_KEY:'02'*32, CONF_AUTH_ID:'01'*4, CONF_APP_ID:'42'}
        result = await self.flow.async_step_manual(data)
        self.assertEqual(result['type'], 'create_entry')
        self.device.connect.assert_not_called()
        data[CONF_PUBLIC_KEY] = '00'*32
        self.assertEqual(validate_credentials(data)[1], {CONF_PUBLIC_KEY:'key_mismatch'})
        data[CONF_APP_ID] = '4294967296'
        self.assertEqual(validate_credentials(data)[1][CONF_APP_ID], 'invalid_app_id')

    async def test_real_manager_duplicate_flow_and_cancellation_cleanup(self):
        started = asyncio.Event()
        async def wait(*args):
            started.set()
            await asyncio.Event().wait()
        self.device.pair.side_effect = wait
        first = await self.manager.async_init(DOMAIN, context={'source':'user'}, data=INPUT)
        duplicate = await self.manager.async_init(DOMAIN, context={'source':'user'}, data=INPUT)
        self.assertEqual(duplicate['reason'], 'already_in_progress')
        fid = first['flow_id']
        form = await self.manager.async_configure(fid, {'next_step_id':'pair'})
        self.assertEqual(form['step_id'], 'pair')
        progress = await self.manager.async_configure(fid, {})
        self.assertEqual(progress['type'], 'progress')
        await started.wait()
        task = self.manager._progress[fid]._task
        self.manager.async_abort(fid)
        await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(task.cancelled())
        self.device.disconnect.assert_awaited_once()
        self.assertFalse(self.manager.async_progress())

    async def test_real_manager_completes_progress_and_entry(self):
        first = await self.manager.async_init(DOMAIN, context={'source':'user'}, data=INPUT)
        fid = first['flow_id']
        await self.manager.async_configure(fid, {'next_step_id':'pair'})
        await self.manager.async_configure(fid, {})
        await self.manager._progress[fid]._task
        with patch.object(self.hass.config_entries, 'async_add', AsyncMock()):
            result = await self.manager.async_configure(fid)
            if result['type'] == 'progress_done':
                result = await self.manager.async_configure(fid)
        self.assertEqual(result['type'], 'create_entry')
        self.assertEqual(result['result'].unique_id, 'aabbccddeeff')
        self.assertFalse(self.manager.async_progress())


class InputTests(unittest.TestCase):
    def test_pin_bounds_and_empty(self):
        for value in ('', ' ', '0', '00000', '65535'):
            self.assertTrue(normalize_pin({CONF_PIN:value}))
        for value in ('65536', '-1', '1.5', '123456', '１２'):
            self.assertFalse(normalize_pin({CONF_PIN:value}))


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def device(self):
        device = PairingNukiDevice(address=ADDRESS, auth_id=None, nuki_public_key=None,
            bridge_public_key=b'\x01'*32, bridge_private_key=b'\x02'*32,
            app_id=42, name='Test')
        device._const = NukiLockConst
        device.connect = AsyncMock()
        device._client = SimpleNamespace(write_gatt_char=AsyncMock(), is_connected=True)
        return device

    async def test_real_library_late_failure_retains_verifiable_credentials(self):
        device = self.device()
        device._device_type = NukiConst.NukiDeviceType.SMARTLOCK_3_4
        device._client.disconnect = AsyncMock()
        remote_key = bytes(PrivateKey.generate().public_key)
        responses = [
            {'public_key': remote_key}, {'nonce': b'n'*32},
            {'nonce': b'n'*32}, {'auth_id': b'i'*4, 'nonce': b'n'*32},
            TimeoutError(),
        ]
        with patch('custom_components.hass_nuki_bt.status_reconnect.StatusReconnectNukiDevice._send_command', AsyncMock(side_effect=responses)):
            with self.assertRaises(TimeoutError):
                await device.pair()
        self.assertTrue(device.authorization_started)
        self.assertEqual(device.pairing_credentials(), {'auth_id':b'i'*4, 'nuki_public_key':remote_key})

    async def test_zero_retry_still_sends_only_once(self):
        device = self.device()
        device.command_response_timeout = .001
        with self.assertRaises(TimeoutError):
            await device._send_command('test', b'test', expected_response=device._const.NukiCommand.AUTHORIZATION_ID, response_retry=0)
        self.assertEqual(device._client.write_gatt_char.await_count, 1)
        self.assertTrue(device.authorization_started)

    async def test_cancel_does_not_retransmit_authorization(self):
        device = self.device()
        sent = asyncio.Event()
        async def send(*args, **kwargs):
            sent.set()
        device._client.write_gatt_char.side_effect = send
        task = asyncio.create_task(device._send_command('test', b'test', expected_response=device._const.NukiCommand.AUTHORIZATION_ID))
        await sent.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(device._client.write_gatt_char.await_count, 1)

    async def test_disconnect_failure_keeps_client_reference(self):
        device = self.device()
        client = device._client
        client.disconnect = AsyncMock()
        with self.assertRaises(BleakError):
            await device.disconnect()
        self.assertIs(device._client, client)
        client.is_connected = False
        await device.disconnect()
        self.assertIsNone(device._client)

    async def test_cleanup_has_deadline(self):
        device = self.device()
        async def wait():
            await asyncio.Event().wait()
        device._client.disconnect = wait
        with patch('custom_components.hass_nuki_bt.pairing.CLEANUP_TIMEOUT', .01):
            with self.assertRaises(TimeoutError):
                await device.disconnect()
