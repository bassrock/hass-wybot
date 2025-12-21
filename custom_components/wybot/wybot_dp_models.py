from enum import Enum
import logging

from pydantic import v1 as pydantic_v1

_LOGGER = logging.getLogger(__name__)


class DP(pydantic_v1.BaseModel):
    """Represents the response for a device command operation."""

    # Represents a data point for a command.
    # 0 - Cleaning Start/Stop (03 - cleaning, 01 - stopped, 02 returning - to dock)
    # 1 - Cleaning Mode
    # 50 - Charge status (First 2, 01 charging, 02 - charged, second 2 digits = charge level)
    id: int

    # All our none if we are requesting data
    type: int | None
    len: int | None
    data: str | None


class GenericDP:
    id: int

    # Type of data
    # 0, len =2, take value of length as hex
    # 4 = 00, 01, 02...  basically convert to simple int
    # 5 = string that looks like hex
    type: int
    len: int
    data: str | None

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
    CLEANING = 3
    STARTING = 255
    UNKNOWN = 15


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
    DOCKED = 0  # Device is docked
    RETURNING = 1  # Device is returning to dock
    GENERAL = 3  # General/unknown status


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
        "Wall then Foor",
        "Standard Full-Pool",
        "Water Line",
        "Strong Floor",
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


# Mapping of types to classes
wybot_dp_id = {
    0: CleaningStatus,
    1: CleaningMode,
    11: Dock,  # Docking status
    13: GenericDP,
    15: GenericDP,
    50: Battery,
    77: GenericDP,
    79: GenericDP,
    131: GenericDP,
    209: GenericDP,
    213: GenericDP,
    214: GenericDP,
    # Add more mappings as needed
}
