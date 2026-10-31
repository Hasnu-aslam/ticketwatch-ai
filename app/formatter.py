"""
Message formatting utilities for Telegram alerts and availability summaries.
Follows clean, high-contrast visual styling:
- 🟢 Green indicator for confirmed available and RAC seats.
- 🔴 Red indicator for sold out, regret, or unavailable options.
- 🟡 Amber indicator for high-probability waitlists.
- Automatically filters out low-chance waitlists (prediction < 50% or WL > 15).
"""

from __future__ import annotations

from typing import Optional
from app.db import Watch
from app.diff import DiffResult
from app.models import SeatStatusType, TravelMode, TravelOption


def format_train_option(opt: TravelOption) -> Optional[str]:
    """
    Formats a single train class option.
    Returns None if it is a low-chance waitlist that should be omitted.
    """
    fare_str = f" | ₹{int(opt.fare)}" if opt.fare else ""
    seats = opt.available_seats

    if opt.status_type == SeatStatusType.AVAILABLE:
        seat_desc = f"{seats} seats available" if seats is not None else "Seats Available"
        return f"  🟢 *{opt.travel_class}:* {seat_desc}{fare_str}"

    if opt.status_type == SeatStatusType.RAC:
        rac_desc = f"RAC {seats}" if seats is not None else "RAC Available"
        return f"  🟢 *{opt.travel_class}:* {rac_desc}{fare_str}"

    if opt.status_type == SeatStatusType.WAITLIST:
        pred = opt.prediction_percentage or opt.extra.get("prediction_percentage")
        # Check if prediction is available and high
        if pred is not None:
            try:
                pred_val = float(pred)
                if pred_val >= 50.0:
                    wl_str = f"WL {opt.waitlist_number}" if opt.waitlist_number else opt.status_detail
                    return f"  🟡 *{opt.travel_class}:* {wl_str} ({int(pred_val)}% confirm chance){fare_str}"
                else:
                    # Low chance (< 50%) -> Do not show
                    return None
            except (ValueError, TypeError):
                pass

        # If no prediction percentage, only show low waitlists (<= 15)
        if opt.waitlist_number is not None and opt.waitlist_number <= 15:
            return f"  🟡 *{opt.travel_class}:* WL {opt.waitlist_number} (Fair chance){fare_str}"

        # Otherwise high waitlist without good prediction -> Do not show
        return None

    # Other statuses (REGRET, NOT_AVAILABLE)
    return None


def format_bus_option(opt: TravelOption) -> str:
    """
    Formats a bus option with clear 🟢 / 🔴 indicator, operator, class, and seats.
    """
    fare_str = f" | ₹{int(opt.fare)}" if opt.fare else ""
    timing_str = f"({opt.departure_time} ➔ {opt.arrival_time})"

    if opt.is_bookable():
        seats = opt.available_seats
        seat_desc = f"{seats} seats left" if seats is not None else "Available"
        return f"🚌 *{opt.operator_name}*\n  🟢 {opt.travel_class} {timing_str}: *{seat_desc}*{fare_str}"
    else:
        return f"🚌 *{opt.operator_name}*\n  🔴 {opt.travel_class} {timing_str}: *Sold Out*{fare_str}"


def format_watch_overview(
    watch: Watch,
    options: list[TravelOption],
    diffs: Optional[list[DiffResult]] = None,
    max_trains: int = 5,
    max_buses: int = 4,
) -> str:
    """
    Generates a structured, easy-to-read overview of current options.
    """
    lines: list[str] = [
        f"📍 *Route:* {watch.source} ➔ {watch.destination}",
        f"📅 *Date:* {watch.travel_date} | 🎯 *Mode:* {watch.mode.upper()}",
    ]

    if diffs:
        lines.append(f"🚨 *Live Updates Detected:* {len(diffs)}")

    lines.append("")

    # 1. TRAINS
    if watch.mode in ("train", "both"):
        lines.append("🚆 *TRAINS (Indian Railways)*")
        lines.append("━" * 28)

        train_options = [o for o in options if o.mode == TravelMode.TRAIN]

        if not train_options:
            lines.append("🔴 *No train data available for this date.*")
        else:
            # Group by train (operator_name)
            train_groups: dict[str, list[TravelOption]] = {}
            for t in train_options:
                train_groups.setdefault(t.operator_name, []).append(t)

            # Sort trains: trains with bookable or promising options first
            def train_sort_key(item: tuple[str, list[TravelOption]]) -> tuple[int, str]:
                has_avail = any(opt.is_bookable() for opt in item[1])
                has_promising = any(opt.is_promising_or_available() for opt in item[1])
                priority = 0 if has_avail else (1 if has_promising else 2)
                return (priority, item[0])

            sorted_trains = sorted(train_groups.items(), key=train_sort_key)

            displayed_train_count = 0
            for train_name, t_opts in sorted_trains[:max_trains]:
                displayed_train_count += 1
                dep = t_opts[0].departure_time
                arr = t_opts[0].arrival_time
                timing = f"({dep} ➔ {arr})" if dep and arr else ""

                # Format classes
                class_lines = []
                for opt in t_opts:
                    formatted_cls = format_train_option(opt)
                    if formatted_cls:
                        class_lines.append(formatted_cls)

                lines.append(f"🚆 *{train_name}* {timing}")
                if class_lines:
                    lines.extend(class_lines)
                else:
                    # All classes were either sold out or low-chance waitlist
                    lines.append("  🔴 *Sold Out / Low WL chance* (filtered)")

            remaining_trains = len(sorted_trains) - max_trains
            if remaining_trains > 0:
                lines.append(f"_...and {remaining_trains} more trains on this route._")

        lines.append("")

    # 2. BUSES (TNSTC / SETC & Private)
    if watch.mode in ("bus", "both"):
        bus_options = [o for o in options if o.mode == TravelMode.BUS]

        # Separate Govt (TNSTC / SETC) and Private
        govt_buses = [
            b for b in bus_options
            if "tnstc" in b.operator_name.lower() or "setc" in b.operator_name.lower() or b.extra.get("bus_type") == "govt"
        ]
        private_buses = [b for b in bus_options if b not in govt_buses]

        # 2a. Government Buses
        lines.append("🚌 *GOVERNMENT BUSES (TNSTC / SETC)*")
        lines.append("━" * 28)
        if not govt_buses:
            lines.append("🔴 *No direct services found on TNSTC portal.*")
        else:
            # Sort available first
            sorted_govt = sorted(govt_buses, key=lambda x: (not x.is_bookable(), x.departure_time))
            for b in sorted_govt[:max_buses]:
                lines.append(format_bus_option(b))
            if len(sorted_govt) > max_buses:
                lines.append(f"_...and {len(sorted_govt) - max_buses} more SETC/TNSTC services._")

        lines.append("")

        # 2b. Private Buses
        lines.append("🚌 *PRIVATE BUSES (RedBus / Operators)*")
        lines.append("━" * 28)
        if not private_buses:
            lines.append("ℹ️ _Automated scan unavailable due to bot protection._")
        else:
            sorted_pvt = sorted(private_buses, key=lambda x: (not x.is_bookable(), x.departure_time))
            for b in sorted_pvt[:max_buses]:
                lines.append(format_bus_option(b))
            if len(sorted_pvt) > max_buses:
                lines.append(f"_...and {len(sorted_pvt) - max_buses} more private bus services._")

    return "\n".join(lines).strip()
