"""Provides response models for the Wybot API."""

from pydantic import BaseModel, Field


def to_snake_case(string: str) -> str:
    """Convert a string from camelCase to snake_case.

    Args:
        string (str): The input string in camelCase.

    Returns:
        str: The converted string in snake_case.

    """
    return "".join(["_" + i.lower() if i.isupper() else i for i in string]).lstrip("_")


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

    class Config:
        """Represents the configuration options for the class."""

        alias_generator = to_snake_case
        allow_population_by_field_name = True


class Docker(BaseModel):
    """Represents a Docker container's information including identifiers, status, and schedule."""

    docker_id: str = Field(alias="dockerId")
    docker_type: str = Field(alias="dockerType")
    ble_name: str = Field(alias="bleName")
    device_status: str = Field(alias="deviceStatus")
    # 3: docked and charging
    # 2: docked and charged
    # 1: returning to dock
    # 0: not docked
    docker_status: str = Field(alias="dockerStatus")
    schedule: str | None = Field(alias="schedule")

    class Config:
        """Represents the configuration options for the class."""

        alias_generator = to_snake_case
        allow_population_by_field_name = True


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

    docker: Docker
    device: Device
    vision: Vision
    name: str
    id: str
    auto_update: str = Field(alias="autoUpdate")

    class Config:
        """Represents the configuration options for the class."""

        alias_generator = to_snake_case
        allow_population_by_field_name = True


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
