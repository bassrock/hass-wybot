"""Provides response models for the Wybot HTTP and MQTT API."""

from typing import TypeVar

from pydantic import BaseModel, Field

from .wybot_dp_models import DP, GenericDP, wybot_dp_id


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
        return {dp.id: wybot_dp_id.get(dp.id, GenericDP)(dp) for dp in self.dp}

    class Config:
        """Represents the configuration options for the class."""

        alias_generator = to_snake_case
        allow_population_by_field_name = True


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

    class Config:
        """Represents the configuration options for the class."""

        alias_generator = to_snake_case
        allow_population_by_field_name = True


class LoginResponse(BaseModel):
    """Represents the response for a login operation."""

    code: int
    reason: str
    message: str
    metadata: LoginMetadata | None = None


class Version(BaseModel):
    """Represents the firmware version information for a device."""

    firmware: str = Field(alias="Firmware")

    class Config:
        """Represents the configuration options for the class."""

        alias_generator = to_snake_case
        allow_population_by_field_name = True


class Device(BaseModel):
    """Represents a device's information including identifiers, type, and version."""

    device_id: str = Field(alias="deviceId")
    device_name: str = Field(alias="deviceName")
    device_type: str = Field(alias="deviceType")
    ble_name: str = Field(alias="bleName")
    version: Version
    pool_id: str | None = Field(alias="poolId")
    auto_update: str = Field(alias="autoUpdate")

    "Extra added fields"
    online: bool = False
    dps: dict[str, DP] = {}

    T = TypeVar("T", bound=GenericDP)

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
        for [id, dp] in self.dps.items():
            if isinstance(dp, cls):
                return dp
        return None

    class Config:
        """Represents the configuration options for the class."""

        alias_generator = to_snake_case
        allow_population_by_field_name = True
        arbitrary_types_allowed = True


class Docker(BaseModel):
    """Represents a Docker container's information including identifiers, status, and schedule."""

    docker_id: str = Field(alias="dockerId")
    docker_type: str = Field(alias="dockerType")
    ble_name: str = Field(alias="bleName")
    device_status: str = Field(alias="deviceStatus")
    docker_status: str = Field(alias="dockerStatus")
    schedule: str | None = Field(alias="schedule")

    "Extra added fields"
    online: bool = False
    dps: dict[str, DP] = {}

    class Config:
        """Represents the configuration options for the class."""

        alias_generator = to_snake_case
        allow_population_by_field_name = True
        arbitrary_types_allowed = True

    T = TypeVar("T", bound=GenericDP)

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
        for [id, dp] in self.dps.items():
            if isinstance(dp, cls):
                return dp
        return None


class Vision(BaseModel):
    """Represents vision-related information including privacy settings, logs, and media."""

    vision_id: str | None = Field(alias="visionId")
    privacy: bool
    log: str | None
    video: str | None
    picture: str | None
    policy: bool

    class Config:
        """Represents the configuration options for the class."""

        alias_generator = to_snake_case
        allow_population_by_field_name = True


class Group(BaseModel):
    """Represents a group containing Docker, Device, and Vision information."""

    docker: Docker | None
    device: Device
    vision: Vision
    name: str
    id: str
    auto_update: str = Field(alias="autoUpdate")

    class Config:
        """Represents the configuration options for the class."""

        alias_generator = to_snake_case
        allow_population_by_field_name = True
        arbitrary_types_allowed = True

    T = TypeVar("T", bound=GenericDP)

    def get_dp(self, cls: type[T]) -> T | None:
        if not issubclass(cls, GenericDP):
            raise TypeError(
                f"The class {cls.__name__} does not inherit from BaseClass."
            )
        for [id, dp] in self.device.dps.items():
            if isinstance(dp, cls):
                return dp
        if self.docker is not None:
            for [id, dp] in self.docker.dps.items():
                if isinstance(dp, cls):
                    return dp
        return None


class DeviceMetadata(BaseModel):
    """Represents metadata containing a list of groups."""

    groups: list[Group]

    class Config:
        """Represents the configuration options for the class."""

        alias_generator = to_snake_case
        allow_population_by_field_name = True


class DevicesResponse(BaseModel):
    """Represents the API response for devices containing status code, reason, message, and metadata."""

    code: int
    reason: str
    message: str
    metadata: DeviceMetadata

    class Config:
        """Represents the configuration options for the class."""

        alias_generator = to_snake_case
        allow_population_by_field_name = True
