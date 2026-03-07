from enum import Enum
import logging

from pydantic import BaseModel

_LOGGER = logging.getLogger(__name__)


class DP(BaseModel):
    """Represents the response for a device command operation."""

    # Represents a data point for a command.
    # 0 - Cleaning Start/Stop (03 - cleaning, 01 - stopped, 02 returning - to dock)
    # 1 - Cleaning Mode
    # 50 - Charge status (First 2, 01 charging, 02 - charged, second 2 digits = charge level)
    id: int

    # All our none if we are requesting data
    type: int | None = None
    len: int | None = None
    data: str | None = None


class GenericDP:
    id: int

    # Type of data
    # 0, len =2, take value of length as hex
    # 4 = 00, 01, 02...  basically convert to simple int
    # 5 = string that looks like hex
    type: int
    len: int
    data: str | None = None

    def __init__(self, data: DP) -> None:
        self.id = data.id
        if data.type is not None:
            self.type = data.type
        if data.len is not None:
            self.len = data.len
        self.data = data.data

    def dict(self) -> dict:
        return {"id": self.id, "type": self.type, "len": self.len, "data": self.data}

    def __str__(self):
        return f"({__class__.__name__}, value={self.dict()})"

    def __repr__(self):
        return f"({__class__.__name__}, value={self.dict()})"


class CleaningStatusMode(Enum):
    STOPPED = 1
    RETURNING = 2  # Legacy/intermediate returning state
    CLEANING = 3
    RETURNING_TO_DOCK = 4  # Returning after "return to dock" command
    UNKNOWN = 15
    STARTING = 255


# Send 03 to start
# Send 01 to stop
class CleaningStatus(GenericDP):
    id = 0
    type = 4
    len = 1

    def __init__(self, data: DP | None = None, status: CleaningStatusMode | None = None) -> None:
        if data is not None:
            super().__init__(data)
        if status is not None:
            self.status = status

    @property
    def status(self) -> CleaningStatusMode:
        if self.data is None:
            return CleaningStatusMode.UNKNOWN
        return CleaningStatusMode(int(self.data, 16))

    @status.setter
    def status(self, data: CleaningStatusMode):
        self.data = f"{int(data.value):02x}"

    def __str__(self):
        return f"({__class__.__name__}, status={self.status})"

    def __repr__(self):
        return f"({__class__.__name__}, status={self.status})"


class DockStatus(Enum):
    DOCKED = 0  # Robot is docked/idle
    RETURNING = 1
    GENERAL = 3


#  Send 01 to go back to dock
class Dock(GenericDP):
    id = 11
    type = 4
    len = 1  # can be 2 when recieving, no idea what the first characters represent

    def __init__(self, data: DP | None = None, status: DockStatus | None = None) -> None:
        if data is not None:
            super().__init__(data)
        if status is not None:
            self.status = status

    @property
    def status(self) -> DockStatus:
        """Return the status of the dock. Note, not really sure how to read this, other then send it comamnd 01 to return to dock."""
        if self.data is None:
            return DockStatus.GENERAL  # Default to general status if no data
        return DockStatus(int(self.data[-2:], 16))

    @status.setter
    def status(self, data: DockStatus):
        self.data = f"{int(data.value):02x}"

    def __str__(self):
        return f"({__class__.__name__}, status={self.data})"

    def __repr__(self):
        return f"({__class__.__name__}, status={self.data})"


class CleaningMode(GenericDP):
    id = 1
    type = 4
    len = 1
    CLEANING_MODES = [
        "Floor",
        "Wall",
        "Wall Then Floor",
        "Advanced Full Pool",
        "Water Line",
        "Turbo Floor",
        "Eco Floor",
    ]

    def __init__(self, data: DP | None = None, mode: str | None = None) -> None:
        if data is not None:
            super().__init__(data)
        if mode is not None:
            self.cleaning_mode = mode

    @property
    def cleaning_mode(self) -> str:
        if self.data is None:
            return self.CLEANING_MODES[0]  # Default to first mode if no data
        return self.CLEANING_MODES[int(self.data, 16)]

    @cleaning_mode.setter
    def cleaning_mode(self, data: str):
        self.data = f"{self.CLEANING_MODES.index(data):02x}"

    def __str__(self):
        return f"({__class__.__name__}, mode={self.cleaning_mode})"

    def __repr__(self):
        return f"({__class__.__name__}, mode={self.cleaning_mode})"


class BatteryState(Enum):
    NOT_PLUGGED_IN = 0
    CHARGING = 1
    CHARGED = 2


class Battery(GenericDP):
    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def battery_level(self) -> int:
        # get the last 2 characters of the data of battery_level and convert from hex to decimal
        if self.data is None:
            return 0
        return int(self.data[-2:], 16)

    @property
    def charge_state(self) -> BatteryState:
        # get the first 2 digits of battery_property and convert from hex to decimal
        if self.data is None:
            return BatteryState.NOT_PLUGGED_IN
        return BatteryState(int(self.data[:2], 16))

    def __str__(self):
        return f"({__class__.__name__}, charge_state={self.charge_state}, battery_level={self.battery_level})"

    def __repr__(self):
        return f"({__class__.__name__}, charge_state={self.charge_state}, battery_level={self.battery_level})"


class SolarEnergyHarvested(GenericDP):
    """DP 131: Total solar energy harvested in Wh (little-endian 4-byte value)."""

    id = 131
    type = 2
    len = 4

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def energy_wh(self) -> int:
        """Return total solar energy harvested in Wh."""
        if self.data is None or len(self.data) < 8:
            return 0
        # Data is little-endian, convert to int
        return int.from_bytes(bytes.fromhex(self.data), byteorder="little")

    @property
    def energy_kwh(self) -> float:
        """Return total solar energy harvested in kWh."""
        return self.energy_wh / 1000.0

    def __str__(self):
        return f"({__class__.__name__}, energy_wh={self.energy_wh}, energy_kwh={self.energy_kwh})"

    def __repr__(self):
        return f"({__class__.__name__}, energy_wh={self.energy_wh}, energy_kwh={self.energy_kwh})"


class SolarDockBattery(GenericDP):
    """DP 221: Solar dock battery level (3-byte format).

    Data format: XXYYZZ (3 bytes / 6 hex chars)
    - XX: Status/flags byte (typically 01)
    - YY: Battery percentage (0-100)
    - ZZ: Unknown (possibly voltage related)

    Example: "01480a" = 72% battery (0x48 = 72)
    """

    id = 221
    type = 0
    len = 3

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def battery_level(self) -> int:
        """Return solar dock battery level as percentage (0-100)."""
        if self.data is None or len(self.data) < 4:
            return 0
        # Battery percentage is in byte 1 (hex chars 2-3)
        # Example: "01480a" -> "48" -> 72%
        try:
            return int(self.data[2:4], 16)
        except (ValueError, IndexError):
            return 0

    def __str__(self):
        return f"({__class__.__name__}, battery_level={self.battery_level}%)"

    def __repr__(self):
        return f"({__class__.__name__}, battery_level={self.battery_level}%)"


class SolarStatusMode(Enum):
    """Solar charging status modes."""

    NOT_CHARGING = 0
    CHARGING = 1


class SolarStatus(GenericDP):
    """DP 222: Solar charging status (1-byte boolean)."""

    id = 222
    type = 0
    len = 1

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def is_charging(self) -> bool:
        """Return True if solar panel is actively charging."""
        if self.data is None:
            return False
        return int(self.data, 16) == 1

    @property
    def status(self) -> SolarStatusMode:
        """Return the solar charging status mode."""
        if self.data is None:
            return SolarStatusMode.NOT_CHARGING
        return SolarStatusMode(int(self.data, 16))

    def __str__(self):
        return f"({__class__.__name__}, is_charging={self.is_charging})"

    def __repr__(self):
        return f"({__class__.__name__}, is_charging={self.is_charging})"


class DockType(Enum):
    """Dock type based on DP 214 values."""

    UNKNOWN = 0
    STANDARD = 1
    SOLAR = 5  # S2 Pro solar dock appears to report 5


class DockInfo(GenericDP):
    """DP 214: Dock type information."""

    id = 214
    type = 4
    len = 1

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def dock_type(self) -> DockType:
        """Return the dock type."""
        if self.data is None:
            return DockType.UNKNOWN
        try:
            return DockType(int(self.data, 16))
        except ValueError:
            return DockType.UNKNOWN

    @property
    def is_solar_dock(self) -> bool:
        """Return True if this is a solar dock."""
        return self.dock_type == DockType.SOLAR

    def __str__(self):
        return f"({__class__.__name__}, dock_type={self.dock_type}, is_solar_dock={self.is_solar_dock})"

    def __repr__(self):
        return f"({__class__.__name__}, dock_type={self.dock_type}, is_solar_dock={self.is_solar_dock})"


class Schedule(GenericDP):
    """DP 79: Schedule data (12-byte value containing schedule configuration)."""

    id = 79
    type = 2
    len = 12

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def raw_schedule(self) -> str:
        """Return raw schedule data as hex string."""
        return self.data if self.data else ""

    def __str__(self):
        return f"({__class__.__name__}, raw_schedule={self.raw_schedule})"

    def __repr__(self):
        return f"({__class__.__name__}, raw_schedule={self.raw_schedule})"


class DeviceStatus(GenericDP):
    """DP 209: Device status flag."""

    id = 209
    type = 4
    len = 1

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def status_value(self) -> int:
        """Return the raw status value."""
        if self.data is None:
            return 0
        return int(self.data, 16)

    def __str__(self):
        return f"({__class__.__name__}, status_value={self.status_value})"

    def __repr__(self):
        return f"({__class__.__name__}, status_value={self.status_value})"


class ConnectionStatus(GenericDP):
    """DP 212: Connection/communication status."""

    id = 212
    type = 4
    len = 1

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def is_connected(self) -> bool:
        """Return True if device is connected."""
        if self.data is None:
            return False
        return int(self.data, 16) == 1

    def __str__(self):
        return f"({__class__.__name__}, is_connected={self.is_connected})"

    def __repr__(self):
        return f"({__class__.__name__}, is_connected={self.is_connected})"


class DockConnectionStatus(GenericDP):
    """DP 213: Dock connection status."""

    id = 213
    type = 4
    len = 1

    def __init__(self, data: DP) -> None:
        super().__init__(data)

    @property
    def is_docked(self) -> bool:
        """Return True if device is docked."""
        if self.data is None:
            return False
        return int(self.data, 16) == 1

    def __str__(self):
        return f"({__class__.__name__}, is_docked={self.is_docked})"

    def __repr__(self):
        return f"({__class__.__name__}, is_docked={self.is_docked})"


# Mapping of types to classes
wybot_dp_id = {
    0: CleaningStatus,
    1: CleaningMode,
    11: Dock,  # Docking status
    13: GenericDP,  # Unknown 4-byte value
    15: GenericDP,
    50: Battery,
    77: GenericDP,  # Unknown 36-byte data (cleaning map/log?)
    79: Schedule,  # Schedule configuration
    131: SolarEnergyHarvested,  # Total solar energy harvested (Wh)
    209: DeviceStatus,  # Device status flag
    212: ConnectionStatus,  # Connection status
    213: DockConnectionStatus,  # Dock connection status
    214: DockInfo,  # Dock type information
    221: SolarDockBattery,  # Solar dock battery level (%)
    222: SolarStatus,  # Solar charging status
    223: GenericDP,  # Unknown 4-byte value
    # Add more mappings as needed
}
