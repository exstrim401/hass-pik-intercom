"""This component provides basic support for Pik Domofon IP intercoms."""
import asyncio
import logging
from typing import Any, Callable, Dict, Mapping, Optional

from homeassistant.components import ffmpeg
from homeassistant.components.camera import Camera, SUPPORT_STREAM
from homeassistant.helpers.typing import HomeAssistantType

from custom_components.pik_intercom._base import BasePikIntercomDeviceEntity
from custom_components.pik_intercom.api import PikIntercomAPI, PikIntercomException
from custom_components.pik_intercom.const import (
    CONF_RETRIEVAL_ERROR_THRESHOLD,
    DATA_FINAL_CONFIG,
    DOMAIN,
)

__all__ = ("async_setup_entry", "PikIntercomCamera")

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(
    hass: HomeAssistantType, config_entry, async_add_entities
) -> bool:
    """Add a Dahua IP camera from a config entry."""

    config_entry_id = config_entry.entry_id

    _LOGGER.debug(f"Setting up 'camera' platform for entry {config_entry_id}")

    api: PikIntercomAPI = hass.data[DOMAIN][config_entry_id]

    async_add_entities(
        [
            PikIntercomCamera(hass, config_entry_id, intercom_device)
            for intercom_device in api.devices.values()
            if intercom_device.has_camera
        ],
        False,
    )

    return True


class PikIntercomCamera(BasePikIntercomDeviceEntity, Camera):
    """An implementation of a Pik Domofon IP intercom."""

    def __init__(self, hass: HomeAssistantType, *args, **kwargs) -> None:
        """Initialize the Pik Domofon intercom video stream."""
        BasePikIntercomDeviceEntity.__init__(self, *args, **kwargs)
        Camera.__init__(self)

        self.entity_id = f"switch.{self._intercom_device.id}_camera"

        self._failed_retrieval_counter = 0
        self._entity_updater: Optional[Callable] = None
        self._ffmpeg = hass.data[ffmpeg.DATA_FFMPEG]

    @property
    def retrieval_error_threshold(self) -> int:
        return max(
            self.hass.data[DATA_FINAL_CONFIG][self._config_entry_id][
                CONF_RETRIEVAL_ERROR_THRESHOLD
            ],
            5,
        )

    @property
    def icon(self) -> str:
        return "mdi:doorbell-video"

    @property
    def unique_id(self):
        """Return the entity unique ID."""
        intercom_device = self._intercom_device
        return f"intercom_camera_{intercom_device.property_id}_{intercom_device.id}"

    @property
    def supported_features(self) -> int:
        """Return supported features."""
        return SUPPORT_STREAM

    @property
    def motion_detection_enabled(self) -> bool:
        """Camera Motion Detection Status."""
        return False

    @property
    def name(self):
        """Return the name of this camera."""
        intercom_device = self._intercom_device
        return (
            intercom_device.renamed_name
            or intercom_device.human_name
            or intercom_device.name
        )

    @property
    def device_info(self) -> Dict[str, Any]:
        intercom_device = self._intercom_device
        return {
            "name": intercom_device.name,
            "manufacturer": intercom_device.device_category,
            "model": intercom_device.kind + " / " + intercom_device.mode,
            "identifiers": {(DOMAIN, intercom_device.id)},
        }

    @property
    def device_state_attributes(self) -> Mapping[str, Any]:
        intercom_device = self._intercom_device
        intercom_streams = intercom_device.video
        return {
            "photo_url": intercom_device.photo_url,
            "stream_url": intercom_device.stream_url,
            "all_stream_urls": [
                {"quality": key, "source": value}
                for key in (intercom_streams.keys() if intercom_streams else ())
                for value in intercom_streams.getall(key)
            ],
            "face_detection": intercom_device.face_detection,
        }

    def turn_off(self) -> None:
        raise NotImplementedError

    def turn_on(self) -> None:
        raise NotImplementedError

    def enable_motion_detection(self) -> None:
        raise NotImplementedError

    def disable_motion_detection(self) -> None:
        raise NotImplementedError

    def camera_image(
        self, width: Optional[int] = None, height: Optional[int] = None
    ) -> Optional[bytes]:
        return asyncio.run_coroutine_threadsafe(
            self.async_camera_image(width, height),
            self.hass.loop,
        ).result()

    async def async_camera_image(
        self, width: Optional[int] = None, height: Optional[int] = None
    ) -> Optional[bytes]:
        """Return a still image response from the camera."""
        # Send the request to snap a picture and return raw jpg data
        intercom_device = self._intercom_device
        if not intercom_device.photo_url:
            stream_url = intercom_device.stream_url
            if stream_url:
                _LOGGER.debug(f"[{self.entity_id}] Fetching snapshot from video feed")
                snapshot_image = await ffmpeg.async_get_image(
                    self.hass, stream_url, width=width, height=height
                )
                if snapshot_image is None:
                    _LOGGER.warning(
                        f"[{self.entity_id}] Video source did not provide any image"
                    )
                    return None
                _LOGGER.debug(f"[{self.entity_id}] Retrieved snapshot from video feed")
                return snapshot_image
            _LOGGER.warning(f"[{self.entity_id}] No video source available")
            return None

        try:
            snapshot_image = await intercom_device.async_get_snapshot()

        except PikIntercomException as error:
            self._failed_retrieval_counter += 1

            failed_retrieval_counter = self._failed_retrieval_counter
            retrieval_error_threshold = self.retrieval_error_threshold

            if failed_retrieval_counter < retrieval_error_threshold:
                _LOGGER.error(
                    f"[{self.entity_id}] Error "
                    f"({failed_retrieval_counter}/{retrieval_error_threshold}): "
                    f"{error}"
                )
                return None

        else:
            self._failed_retrieval_counter = 0
            return snapshot_image

        self._failed_retrieval_counter = 0
        _LOGGER.error(
            f"[{self.entity_id}] Retrieval error threshold "
            f"({retrieval_error_threshold}) reached. "
            f"Attempting data update to refresh URLs"
        )
        await self.api_object.async_update_property_intercoms(
            intercom_device.property_id
        )

    async def stream_source(self) -> Optional[str]:
        """Return the RTSP stream source."""
        return self._intercom_device.stream_url
