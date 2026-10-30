from __future__ import annotations

import re
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class TravelMode(str, Enum):
    TRAIN = "train"
    BUS = "bus"


class SeatStatusType(str, Enum):
    AVAILABLE = "AVAILABLE"
    RAC = "RAC"
    WAITLIST = "WAITLIST"
    REGRET = "REGRET"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    UNKNOWN = "UNKNOWN"


def parse_seat_status(status_str: Optional[str]) -> tuple[SeatStatusType, Optional[int], Optional[int]]:
    """
    Parses typical IRCTC / Indian Railways / Bus status strings.
    Examples:
      - 'AVAILABLE 12' or 'AVAILABLE-0012' -> (AVAILABLE, 12, None)
      - 'CURR_AVBL-0005' -> (AVAILABLE, 5, None)
      - 'RAC 14' or 'RAC 45/RAC 23' -> (RAC, 23, None)
      - 'GNWL 52/WL 28' or 'PQWL12/WL7' -> (WAITLIST, None, 7 or 28)
      - 'WL 40' -> (WAITLIST, None, 40)
      - 'REGRET' -> (REGRET, None, None)
      - 'TRAIN DEPARTED' or 'NOT AVAILABLE' -> (NOT_AVAILABLE, None, None)
    Returns:
      (status_type, available_seats, waitlist_number)
    """
    if not status_str or not status_str.strip():
        return SeatStatusType.UNKNOWN, None, None

    s = status_str.strip().upper()

    if any(k in s for k in ("AVAILABLE", "CURR_AVBL", "AVL", "AVBL")):
        nums = re.findall(r"\d+", s)
        seats = int(nums[-1]) if nums else None
        return SeatStatusType.AVAILABLE, seats, None

    if "RAC" in s:
        nums = re.findall(r"\d+", s)
        rac_num = int(nums[-1]) if nums else None
        return SeatStatusType.RAC, rac_num, None

    if any(k in s for k in ("WL", "PQWL", "GNWL", "RLWL", "TQWL", "WAITLIST")):
        # If format is 'PQWL12/WL7', extract current WL (7)
        if "/" in s:
            parts = s.split("/")
            nums = re.findall(r"\d+", parts[-1])
            if nums:
                return SeatStatusType.WAITLIST, None, int(nums[-1])
        nums = re.findall(r"\d+", s)
        wl_num = int(nums[-1]) if nums else None
        return SeatStatusType.WAITLIST, None, wl_num

    if any(k in s for k in ("REGRET", "NOT AVAILABLE", "TRAIN CANCELLED", "DEPARTED", "CLASS DOES NOT EXIST")):
        if "REGRET" in s:
            return SeatStatusType.REGRET, None, None
        return SeatStatusType.NOT_AVAILABLE, None, None

    return SeatStatusType.UNKNOWN, None, None


class TravelOption(BaseModel):
    """
    A single unified travel option (e.g. one train class or one bus).
    """
    id: str  # Unique stable identifier for diffing, e.g. 'TRAIN_12635_3A_GN' or 'BUS_KPN_2130'
    mode: TravelMode
    operator_name: str  # e.g. '12635 - VAIGAI SF EXP' or 'IntrCity SmartBus'
    train_number: Optional[str] = None
    source: str
    destination: str
    travel_date: str  # YYYY-MM-DD
    departure_time: str  # HH:MM
    arrival_time: str  # HH:MM
    duration: Optional[str] = None  # e.g. '07h 30m'
    travel_class: str  # 'SL', '3A', '2A', 'AC Sleeper', 'Seater', etc.
    quota: Optional[str] = "GN"  # 'GN', 'TQ', 'PT'
    status_type: SeatStatusType
    status_detail: str  # e.g. 'AVAILABLE 14', 'PQWL12/WL7'
    available_seats: Optional[int] = None
    waitlist_number: Optional[int] = None
    fare: Optional[float] = None
    booking_url: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)

    def is_bookable(self) -> bool:
        """Returns True if the ticket is confirmed available or RAC."""
        return self.status_type in (SeatStatusType.AVAILABLE, SeatStatusType.RAC)


class SearchQuery(BaseModel):
    """
    Normalized search parameters for fetchers.
    """
    source: str  # City name or Station code
    destination: str
    travel_date: str  # YYYY-MM-DD
    mode: Optional[TravelMode] = None
    classes: Optional[list[str]] = None  # ['SL', '3A'] or ['AC Sleeper']
    max_price: Optional[float] = None
    departure_after: Optional[str] = None  # 'HH:MM'
    departure_before: Optional[str] = None  # 'HH:MM'
    quota: Optional[str] = "GN"  # 'GN', 'TQ', 'PT'
