from enum import Enum
from typing import Optional
from pydantic import BaseModel

from app.db import Snapshot, Watch
from app.models import SeatStatusType, TravelOption


class DiffChangeType(str, Enum):
    SEAT_OPENED = "SEAT_OPENED"              # WL/REGRET/NOT_AVAILABLE -> AVAILABLE
    RAC_PROMOTED = "RAC_PROMOTED"            # WL -> RAC
    WAITLIST_IMPROVED = "WAITLIST_IMPROVED"  # Significant WL drop (>= 10 positions)
    PRICE_DROPPED = "PRICE_DROPPED"          # Fare drop >= ₹100 or >= 10%
    SEATS_RUNNING_OUT = "SEATS_RUNNING_OUT"  # Available seats dropped to <= 5
    NEW_AVAILABLE = "NEW_AVAILABLE"          # New service added with available seats


class DiffResult(BaseModel):
    watch_id: int
    option: TravelOption
    change_type: DiffChangeType
    previous_detail: Optional[str] = None
    current_detail: str
    message: str
    is_urgent: bool = False


class DiffEngine:
    """
    Evaluates current travel options against the previous snapshot.
    Only triggers alerts for meaningful changes to eliminate notification spam.
    """

    MIN_PRICE_DROP_INR: float = 100.0
    MIN_PRICE_DROP_PCT: float = 0.10
    MIN_WL_DROP: int = 10
    LOW_SEATS_THRESHOLD: int = 5

    def compute_diffs(
        self,
        watch: Watch,
        current_options: list[TravelOption],
        previous_snapshots: dict[str, Snapshot],
        is_initial_check: bool = False,
    ) -> list[DiffResult]:
        """
        Compares current_options against previous_snapshots.
        Returns a list of DiffResult for items that qualify for user alerts.
        """
        diffs: list[DiffResult] = []

        for opt in current_options:
            prev = previous_snapshots.get(opt.id)

            # Case A: Brand new option not in previous snapshot
            if prev is None:
                if opt.is_bookable():
                    # If this option is available, alert user
                    diffs.append(
                        DiffResult(
                            watch_id=watch.id,
                            option=opt,
                            change_type=DiffChangeType.NEW_AVAILABLE,
                            previous_detail=None,
                            current_detail=opt.status_detail,
                            message=f"🎉 New bookable option found! {opt.operator_name} ({opt.travel_class}): {opt.status_detail}",
                            is_urgent=True,
                        )
                    )
                continue

            prev_type = SeatStatusType(prev.status_type)
            curr_type = opt.status_type

            # Case B: Status Transition to AVAILABLE (Seat Opened)
            if prev_type != SeatStatusType.AVAILABLE and curr_type == SeatStatusType.AVAILABLE:
                diffs.append(
                    DiffResult(
                        watch_id=watch.id,
                        option=opt,
                        change_type=DiffChangeType.SEAT_OPENED,
                        previous_detail=prev.status_detail,
                        current_detail=opt.status_detail,
                        message=f"🟢 SEAT OPENED! {opt.operator_name} ({opt.travel_class}) moved from {prev.status_detail} ➔ {opt.status_detail}",
                        is_urgent=True,
                    )
                )
                continue

            # Case C: Status Transition to RAC
            if prev_type == SeatStatusType.WAITLIST and curr_type == SeatStatusType.RAC:
                diffs.append(
                    DiffResult(
                        watch_id=watch.id,
                        option=opt,
                        change_type=DiffChangeType.RAC_PROMOTED,
                        previous_detail=prev.status_detail,
                        current_detail=opt.status_detail,
                        message=f"🎫 RAC Available! {opt.operator_name} ({opt.travel_class}) moved from {prev.status_detail} ➔ {opt.status_detail}",
                        is_urgent=False,
                    )
                )
                continue

            # Case D: Significant Waitlist Drop
            if prev_type == SeatStatusType.WAITLIST and curr_type == SeatStatusType.WAITLIST:
                if prev.waitlist_number and opt.waitlist_number:
                    wl_drop = prev.waitlist_number - opt.waitlist_number
                    if wl_drop >= self.MIN_WL_DROP:
                        diffs.append(
                            DiffResult(
                                watch_id=watch.id,
                                option=opt,
                                change_type=DiffChangeType.WAITLIST_IMPROVED,
                                previous_detail=prev.status_detail,
                                current_detail=opt.status_detail,
                                message=f"📈 Significant Waitlist improvement for {opt.operator_name}: WL dropped by {wl_drop} positions ({prev.status_detail} ➔ {opt.status_detail})",
                                is_urgent=False,
                            )
                        )
                        continue

            # Case E: Urgency - Available seats dropped below threshold
            if (
                prev_type == SeatStatusType.AVAILABLE
                and curr_type == SeatStatusType.AVAILABLE
                and prev.available_seats is not None
                and opt.available_seats is not None
            ):
                if prev.available_seats > self.LOW_SEATS_THRESHOLD and opt.available_seats <= self.LOW_SEATS_THRESHOLD:
                    diffs.append(
                        DiffResult(
                            watch_id=watch.id,
                            option=opt,
                            change_type=DiffChangeType.SEATS_RUNNING_OUT,
                            previous_detail=prev.status_detail,
                            current_detail=opt.status_detail,
                            message=f"⚠️ Hurry! Only {opt.available_seats} seats left for {opt.operator_name} ({opt.travel_class})",
                            is_urgent=True,
                        )
                    )
                    continue

            # Case F: Price Drop
            if prev.fare is not None and opt.fare is not None:
                price_drop = prev.fare - opt.fare
                pct_drop = price_drop / prev.fare if prev.fare > 0 else 0
                if price_drop >= self.MIN_PRICE_DROP_INR or pct_drop >= self.MIN_PRICE_DROP_PCT:
                    diffs.append(
                        DiffResult(
                            watch_id=watch.id,
                            option=opt,
                            change_type=DiffChangeType.PRICE_DROPPED,
                            previous_detail=f"₹{int(prev.fare)}",
                            current_detail=f"₹{int(opt.fare)}",
                            message=f"💸 Price dropped by ₹{int(price_drop)}! {opt.operator_name} is now ₹{int(opt.fare)} (was ₹{int(prev.fare)})",
                            is_urgent=False,
                        )
                    )
                    continue

        return diffs
