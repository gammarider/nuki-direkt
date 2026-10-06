"""Guided Nuki setup with bounded BLE work and conservative retry handling."""
from __future__ import annotations

import asyncio
import re
import secrets

from bleak import BleakError
from nacl.public import PrivateKey
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.const import CONF_NAME, CONF_PIN
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode
from pyNukiBT import NukiConst, NukiErrorException

from .const import (
    CONF_APP_ID, CONF_AUTH_ID, CONF_DEVICE_ADDRESS, CONF_DEVICE_PUBLIC_KEY,
    CONF_PRIVATE_KEY, CONF_PUBLIC_KEY, CONF_CLIENT_TYPE, CONF_STATUS_RECONNECT,
    DEFAULT_STATUS_RECONNECT, DOMAIN, LOGGER,
)
from .pairing import PairingNukiDevice

CONNECT_TIMEOUT = 20
PAIR_TIMEOUT = 60
VERIFY_TIMEOUT = 20


class NukiFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Keep setup credentials private until pairing or verification succeeds."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return NukiOptionsFlow()

    def __init__(self):
        self._data = {}
        self._task = None
        self._device = None
        self._identity = None
        self._result = None
        self._candidate = None
        self._error = None
        self._uncertain = False
        self._cleanup_failed = False
        self._pin_required = False
        self._verifying = False

    async def _async_claim_address(self):
        address = self._data[CONF_DEVICE_ADDRESS]
        # A domain transition must import existing credentials, never pair twice.
        if any(
            str(entry.data.get(CONF_DEVICE_ADDRESS, "")).strip().upper() == address
            for entry in self.hass.config_entries.async_entries("hass_nuki_bt")
        ):
            self._error = "migration_required"
            raise AbortFlow("migration_required")
        # Older user-created entries may have no unique_id at all.
        if any(
            str(entry.data.get(CONF_DEVICE_ADDRESS, "")).strip().upper() == address
            for entry in self._async_current_entries()
        ):
            return False
        await self.async_set_unique_id(format_unique_id(address))
        self._abort_if_unique_id_configured()
        return True

    async def async_step_bluetooth(self, discovery_info):
        self._data[CONF_DEVICE_ADDRESS] = discovery_info.address.strip().upper()
        self._data[CONF_NAME] = discovery_info.name or "Nuki"
        if not await self._async_claim_address():
            return self.async_abort(reason="already_configured")
        self.context["title_placeholders"] = {
            "name": self._data[CONF_NAME], "address": self._data[CONF_DEVICE_ADDRESS],
        }
        return await self.async_step_step1()

    async def async_step_user(self, user_input=None):
        return await self.async_step_step1(user_input)

    async def async_step_step1(self, user_input=None):
        errors = {}
        if user_input is not None:
            data = {**self._data, **user_input}
            data[CONF_DEVICE_ADDRESS] = str(data.get(CONF_DEVICE_ADDRESS, "")).strip().upper()
            data[CONF_NAME] = str(data.get(CONF_NAME, "")).strip()
            if not validate_address(data[CONF_DEVICE_ADDRESS]):
                errors[CONF_DEVICE_ADDRESS] = "invalid_address"
            if not data[CONF_NAME]:
                errors[CONF_NAME] = "required"
            if data.get(CONF_CLIENT_TYPE) not in ("Bridge", "App"):
                errors[CONF_CLIENT_TYPE] = "invalid_client_type"
            if not normalize_pin(data):
                errors[CONF_PIN] = "invalid_pin"
            if not errors:
                self._data = data
                if not await self._async_claim_address():
                    return self.async_abort(reason="already_configured")
                return await self.async_step_choose_method()
        return self.async_show_form(
            step_id="step1", errors=errors,
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=self._data.get(CONF_NAME, "")): str,
                vol.Required(CONF_DEVICE_ADDRESS, default=self._data.get(CONF_DEVICE_ADDRESS, "")): str,
                vol.Optional(CONF_PIN): str,
                vol.Required(CONF_CLIENT_TYPE, default="App"): SelectSelector(
                    SelectSelectorConfig(options=["App", "Bridge"], mode=SelectSelectorMode.DROPDOWN)
                ),
            }),
        )

    async def async_step_choose_method(self, user_input=None):
        return self.async_show_menu(
            step_id="choose_method", menu_options=["pair", "manual"],
            description_placeholders={"name": self._data[CONF_NAME], "address": self._data[CONF_DEVICE_ADDRESS]},
        )

    async def async_step_pair(self, user_input=None):
        if self._uncertain:
            return await self.async_step_recover()
        if self._cleanup_failed:
            return self.async_abort(reason="cleanup_failed")
        if not self._data.get(CONF_DEVICE_ADDRESS):
            return await self.async_step_step1()
        errors = {"base": self._error} if self._error else {}
        self._error = None
        if user_input is not None:
            data = {**self._data, **user_input}
            if not normalize_pin(data):
                errors = {CONF_PIN: "invalid_pin"}
            elif self._pin_required and CONF_PIN not in data:
                errors = {CONF_PIN: "pin_required"}
            else:
                self._data = data
                if not await self._async_claim_address():
                    return self.async_abort(reason="already_configured")
                self._verifying = False
                self._task = self.hass.async_create_task(self._async_work(), eager_start=False)
                return await self.async_step_pairing()
        schema = {vol.Required(CONF_PIN) if self._pin_required else vol.Optional(CONF_PIN): str}
        return self.async_show_form(step_id="pair", data_schema=vol.Schema(schema), errors=errors)

    async def async_step_pairing(self, user_input=None):
        if self._task is None:
            return await self.async_step_pair()
        if not self._task.done():
            return self.async_show_progress(
                step_id="pairing", progress_action="verify" if self._verifying else "pair",
                progress_task=self._task,
            )
        if self._task.cancelled():
            return self.async_abort(reason="pairing_cancelled")
        self._task.result()
        self._task = None
        return self.async_show_progress_done(next_step_id="pair_result")

    async def async_step_pair_result(self, user_input=None):
        if self._result is not None:
            if not await self._async_claim_address():
                return self.async_abort(reason="already_configured")
            return self.async_create_entry(title=self._data[CONF_NAME], data={**self._data, **self._result})
        if self._uncertain:
            return await self.async_step_recover()
        return await self.async_step_pair()

    async def async_step_recover(self, user_input=None):
        if self._candidate is None:
            return self.async_abort(reason="pairing_uncertain")
        if user_input is not None:
            if not await self._async_claim_address():
                return self.async_abort(reason="already_configured")
            self._verifying = True
            self._error = None
            self._task = self.hass.async_create_task(self._async_work(), eager_start=False)
            return await self.async_step_pairing()
        return self.async_show_form(
            step_id="recover", data_schema=vol.Schema({}),
            errors={"base": self._error} if self._error else {},
        )

    def _credentials(self, result):
        return {
            CONF_AUTH_ID: result["auth_id"].hex(),
            CONF_DEVICE_PUBLIC_KEY: result["nuki_public_key"].hex(),
            CONF_PUBLIC_KEY: self._identity["public"].hex(),
            CONF_PRIVATE_KEY: self._identity["private"].hex(),
            CONF_APP_ID: str(self._identity["app_id"]),
        }

    async def _async_work(self):
        self._result = None
        self._error = None
        device = self._device if self._verifying else None
        rejected = False
        try:
            if self._verifying:
                # Release any old client before opening a new read-only session.
                await device.disconnect()
            else:
                ble = bluetooth.async_ble_device_from_address(self.hass, self._data[CONF_DEVICE_ADDRESS], connectable=True)
                if ble is None:
                    self._error = "not_found"
                    return
                if self._identity is None:
                    key = PrivateKey.generate()
                    self._identity = {"private": bytes(key), "public": bytes(key.public_key), "app_id": secrets.randbits(32)}
                device = self._device = PairingNukiDevice(
                    address=self._data[CONF_DEVICE_ADDRESS], auth_id=None, nuki_public_key=None,
                    bridge_public_key=self._identity["public"], bridge_private_key=self._identity["private"],
                    app_id=self._identity["app_id"], name="HomeAssistant",
                    client_type=NukiConst.NukiClientType.APP if self._data[CONF_CLIENT_TYPE] == "App" else NukiConst.NukiClientType.BRIDGE,
                    ble_device=ble, get_ble_device=lambda addr: bluetooth.async_ble_device_from_address(self.hass, addr, connectable=True),
                )
            async with asyncio.timeout(CONNECT_TIMEOUT):
                await device.connect()
            if self._verifying:
                async with asyncio.timeout(VERIFY_TIMEOUT):
                    await device.update_state()
                if not device._last_update_state_successful:
                    raise BleakError("Verification did not receive a valid state")
                self._result = self._candidate
                self._uncertain = False
            else:
                if device._device_type == NukiConst.NukiDeviceType.SMARTLOCK_ULTRA and CONF_PIN not in self._data:
                    self._pin_required = True
                    self._error = "pin_required"
                    return
                async with asyncio.timeout(PAIR_TIMEOUT):
                    result = await device.pair(int(self._data[CONF_PIN]) if CONF_PIN in self._data else None)
                self._result = self._credentials(result)
        except asyncio.CancelledError:
            raise
        except NukiErrorException as ex:
            rejected = str(ex.error_code) in {
                "P_ERROR_NOT_PAIRING", "P_ERROR_MAX_USER",
                "K_ERROR_BAD_PIN", "K_ERROR_TOO_MANY_PIN_ATTEMPTS",
            }
            if str(ex.error_code) in {"K_ERROR_BAD_PIN", "K_ERROR_TOO_MANY_PIN_ATTEMPTS"}:
                self._data.pop(CONF_PIN, None)
                self._pin_required = True
            self._error = {
                "P_ERROR_NOT_PAIRING": "pairing",
                "K_ERROR_BAD_PIN": "invalid_pin",
                "K_ERROR_TOO_MANY_PIN_ATTEMPTS": "pin_locked",
                "P_ERROR_MAX_USER": "authorization_full",
            }.get(str(ex.error_code), "pairing_failed")
        except TimeoutError:
            self._error = "timeout"
        except (BleakError, EOFError, OSError):
            self._error = "connection"
        except Exception:
            # Never log exceptions carrying PINs, keys, or protocol payloads.
            self._error = "unknown"
            LOGGER.warning("Nuki setup failed; no pairing details logged")
        finally:
            if device is not None:
                if self._result is None and device.authorization_started:
                    candidate = device.pairing_credentials()
                    # Explicit rejection without an ID is safe to correct. A
                    # received ID always needs verification, even after an error.
                    self._uncertain = candidate is not None or not rejected
                    if candidate is not None:
                        self._candidate = self._credentials(candidate)
                try:
                    await device.disconnect()
                    self._cleanup_failed = False
                except Exception:
                    self._cleanup_failed = True
                    LOGGER.warning("Nuki setup connection could not be released")

    async def async_step_manual(self, user_input=None):
        errors = {}
        if user_input is not None:
            data, errors = validate_credentials(user_input)
            if not errors:
                if not await self._async_claim_address():
                    return self.async_abort(reason="already_configured")
                return self.async_create_entry(title=self._data[CONF_NAME], data={**self._data, **data})
        return self.async_show_form(
            step_id="manual", errors=errors,
            data_schema=vol.Schema({vol.Required(key): str for key in (
                CONF_AUTH_ID, CONF_PRIVATE_KEY, CONF_PUBLIC_KEY, CONF_DEVICE_PUBLIC_KEY, CONF_APP_ID,
            )}),
        )

class NukiOptionsFlow(config_entries.OptionsFlow):
    """Configure BLE recovery, enabled by default, for one device."""

    async def async_step_init(self, user_input=None):
        """Show and save connection options without starting a pairing flow."""
        if user_input is not None:
            return self.async_create_entry(
                title="", data={**self.config_entry.options, **user_input}
            )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_STATUS_RECONNECT,
                    default=self.config_entry.options.get(
                        CONF_STATUS_RECONNECT, DEFAULT_STATUS_RECONNECT
                    ),
                ): bool,
            }),
        )



def format_unique_id(address: str) -> str:
    """Use the established MAC-based identity for all setup entry points."""
    return address.replace(":", "").lower()


def validate_address(address: str) -> bool:
    return re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", address) is not None


def normalize_pin(data):
    pin = str(data.get(CONF_PIN, "")).strip()
    if not pin:
        data.pop(CONF_PIN, None)
        return True
    if re.fullmatch(r"[0-9]{1,5}", pin) is None or int(pin) > 65535:
        return False
    data[CONF_PIN] = pin
    return True


def validate_credentials(user_input):
    data, errors = {}, {}
    for key, size in ((CONF_AUTH_ID, 4), (CONF_PRIVATE_KEY, 32), (CONF_PUBLIC_KEY, 32), (CONF_DEVICE_PUBLIC_KEY, 32)):
        value = str(user_input.get(key, "")).strip()
        if re.fullmatch(r"[0-9a-fA-F]{" + str(size * 2) + r"}", value) is None:
            errors[key] = "invalid_key"
        else:
            data[key] = value.lower()
    app_id = str(user_input.get(CONF_APP_ID, "")).strip()
    if re.fullmatch(r"[0-9]{1,10}", app_id) is None or int(app_id) > 0xFFFFFFFF:
        errors[CONF_APP_ID] = "invalid_app_id"
    else:
        data[CONF_APP_ID] = str(int(app_id))
    if CONF_PRIVATE_KEY in data and CONF_PUBLIC_KEY in data:
        public = bytes(PrivateKey(bytes.fromhex(data[CONF_PRIVATE_KEY])).public_key).hex()
        if public != data[CONF_PUBLIC_KEY]:
            errors[CONF_PUBLIC_KEY] = "key_mismatch"
    return data, errors
