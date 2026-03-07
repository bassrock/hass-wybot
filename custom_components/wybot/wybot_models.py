"""Provides response models for the Wybot HTTP and MQTT API."""

from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field

from .wybot_dp_models import DP, GenericDP, wybot_dp_id

T = TypeVar("T", bound=GenericDP)


def to_snake_case(string: str) -> str:
    """Convert a string from camelCase to snake_case.

    Args:
        string (str): The input string in camelCase.

    Returns:
        str: The converted string in snake_case.

    """
    return "".join(["_" + i.lower() if i.isupper() else i for i in string]).lstrip("_")


class Command(BaseModel):
    """Represents a command to be sent or received from a device."""

    # 4 - Send Write Command
    # 5 - Data Request Response
    # 9 - Data Request
    cmd: int
    dp: list[DP]
    ts: int

    def get_dps_as_keyed_dict(self) -> dict[str, GenericDP]:
        """Return the DP list as a keyed dictionary."""
        return {str(dp.id): wybot_dp_id.get(dp.id, GenericDP)(dp) for dp in self.dp}

    model_config = ConfigDict(
        alias_generator=to_snake_case,
        populate_by_name=True,
    )


class LoginMetadata(BaseModel):
    """Represents the metadata for a user."""

    user_id: str = Field(alias="userId")
    token: str
    username: str
    name: str
    avatar: str
    groupid: int
    reg_time: int = Field(alias="regTime")
    last_login_time: int = Field(alias="lastLoginTime")

    model_config = ConfigDict(
        alias_generator=to_snake_case,
        populate_by_name=True,
    )


class LoginResponse(BaseModel):
    """Represents the response for a login operation."""

    code: int
    reason: str
    message: str
    metadata: LoginMetadata | None = None


class Version(BaseModel):
    """Represents the firmware version information for a device."""

    firmware: str | None = Field(default=None, alias="Firmware")

    model_config = ConfigDict(
        alias_generator=to_snake_case,
        populate_by_name=True,
    )


class Device(BaseModel):
    """Represents a device's information including identifiers, type, and version."""

    device_id: str = Field(alias="deviceId")
    device_name: str = Field(alias="deviceName")
    device_type: str = Field(alias="deviceType")
    ble_name: str = Field(alias="bleName")
    version: Version | None = None
    pool_id: str | None = Field(default=None, alias="poolId")
    auto_update: str = Field(alias="autoUpdate")

    # Extra added fields
    online: bool = False
    dps: dict[str, DP] = {}

    def get_dp(self, cls: type[T]) -> T | None:
        """Get the specified DP from the device.

        Args:
            cls (Type[T]): The type of DP to retrieve.

        Returns:
            T | None: The specified DP if found, otherwise None.

        """
        if not issubclass(cls, GenericDP):
            raise TypeError(
                f"The class {cls.__name__} does not inherit from BaseClass."
            )
        for [_, dp] in self.dps.items():
            if isinstance(dp, cls):
                return dp
        return None

    model_config = ConfigDict(
        alias_generator=to_snake_case,
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )


class Docker(BaseModel):
    """Represents a Docker container's information including identifiers, status, and schedule."""

    docker_id: str = Field(alias="dockerId")
    docker_type: str = Field(alias="dockerType")
    ble_name: str = Field(alias="bleName")
    device_status: str = Field(alias="deviceStatus")
    docker_status: str = Field(alias="dockerStatus")
    schedule: str | None = Field(default=None, alias="schedule")
    version: Version | None = None

    # Extra added fields
    online: bool = False
    dps: dict[str, DP] = {}

    model_config = ConfigDict(
        alias_generator=to_snake_case,
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )

    def get_dp(self, cls: type[T]) -> T | None:
        """Get the specified DP from the device.

        Args:
            cls (Type[T]): The type of DP to retrieve.

        Returns:
            T | None: The specified DP if found, otherwise None.

        """
        if not issubclass(cls, GenericDP):
            raise TypeError(
                f"The class {cls.__name__} does not inherit from BaseClass."
            )
        for [_, dp] in self.dps.items():
            if isinstance(dp, cls):
                return dp
        return None


class Vision(BaseModel):
    """Represents vision-related information including privacy settings, logs, and media."""

    vision_id: str | None = Field(default=None, alias="visionId")
    privacy: bool
    log: str | None = None
    video: str | None = None
    picture: str | None = None
    policy: bool

    model_config = ConfigDict(
        alias_generator=to_snake_case,
        populate_by_name=True,
    )


class Group(BaseModel):
    """Represents a group containing Docker, Device, and Vision information."""

    docker: Docker | None = None
    device: Device
    vision: Vision
    name: str
    id: str
    auto_update: str = Field(alias="autoUpdate")

    model_config = ConfigDict(
        alias_generator=to_snake_case,
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )

    def get_dp(self, cls: type[T]) -> T | None:
        if not issubclass(cls, GenericDP):
            raise TypeError(
                f"The class {cls.__name__} does not inherit from BaseClass."
            )
        for [_, dp] in self.device.dps.items():
            if isinstance(dp, cls):
                return dp
        if self.docker is not None:
            for [_, dp] in self.docker.dps.items():
                if isinstance(dp, cls):
                    return dp
        return None


class DeviceMetadata(BaseModel):
    """Represents metadata containing a list of groups."""

    groups: list[Group]

    model_config = ConfigDict(
        alias_generator=to_snake_case,
        populate_by_name=True,
    )


class DevicesResponse(BaseModel):
    """Represents the API response for devices containing status code, reason, message, and metadata."""

    code: int
    reason: str
    message: str
    metadata: DeviceMetadata

    model_config = ConfigDict(
        alias_generator=to_snake_case,
        populate_by_name=True,
    )
