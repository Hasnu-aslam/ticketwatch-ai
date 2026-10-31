from app.db import Watch
from app.formatter import (
    format_bus_option,
    format_train_option,
    format_watch_overview,
)
from app.models import SeatStatusType, TravelMode, TravelOption


def test_format_train_option_available():
    opt = TravelOption(
        id="TRAIN_12635_3A_GN",
        mode=TravelMode.TRAIN,
        operator_name="12635 - VAIGAI SF EXP",
        source="MS",
        destination="MDU",
        travel_date="2026-11-08",
        departure_time="13:50",
        arrival_time="21:30",
        travel_class="3A",
        status_type=SeatStatusType.AVAILABLE,
        status_detail="AVAILABLE 14",
        available_seats=14,
        fare=850.0,
    )
    result = format_train_option(opt)
    assert result is not None
    assert "🟢" in result
    assert "3A" in result
    assert "14 seats available" in result
    assert "₹850" in result


def test_format_train_option_rac():
    opt = TravelOption(
        id="TRAIN_12635_SL_GN",
        mode=TravelMode.TRAIN,
        operator_name="12635 - VAIGAI SF EXP",
        source="MS",
        destination="MDU",
        travel_date="2026-11-08",
        departure_time="13:50",
        arrival_time="21:30",
        travel_class="SL",
        status_type=SeatStatusType.RAC,
        status_detail="RAC 8",
        available_seats=8,
        fare=245.0,
    )
    result = format_train_option(opt)
    assert result is not None
    assert "🟢" in result
    assert "RAC 8" in result


def test_format_train_option_low_chance_waitlist_filtered():
    # Low prediction percentage (< 50%) should be omitted (returns None)
    opt_low_pred = TravelOption(
        id="TRAIN_12635_2A_GN",
        mode=TravelMode.TRAIN,
        operator_name="12635 - VAIGAI SF EXP",
        source="MS",
        destination="MDU",
        travel_date="2026-11-08",
        departure_time="13:50",
        arrival_time="21:30",
        travel_class="2A",
        status_type=SeatStatusType.WAITLIST,
        status_detail="GNWL80/WL45",
        waitlist_number=45,
        prediction_percentage=25.0,
    )
    assert format_train_option(opt_low_pred) is None

    # High WL number without prediction should also be omitted
    opt_high_wl = TravelOption(
        id="TRAIN_12635_SL_GN",
        mode=TravelMode.TRAIN,
        operator_name="12635 - VAIGAI SF EXP",
        source="MS",
        destination="MDU",
        travel_date="2026-11-08",
        departure_time="13:50",
        arrival_time="21:30",
        travel_class="SL",
        status_type=SeatStatusType.WAITLIST,
        status_detail="WL 60",
        waitlist_number=60,
    )
    assert format_train_option(opt_high_wl) is None


def test_format_train_option_promising_waitlist():
    # High prediction percentage (>= 50%) should be shown
    opt_high_pred = TravelOption(
        id="TRAIN_12635_3A_GN",
        mode=TravelMode.TRAIN,
        operator_name="12635 - VAIGAI SF EXP",
        source="MS",
        destination="MDU",
        travel_date="2026-11-08",
        departure_time="13:50",
        arrival_time="21:30",
        travel_class="3A",
        status_type=SeatStatusType.WAITLIST,
        status_detail="WL 4",
        waitlist_number=4,
        prediction_percentage=82.0,
        fare=910.0,
    )
    result = format_train_option(opt_high_pred)
    assert result is not None
    assert "🟡" in result
    assert "WL 4" in result
    assert "82% confirm chance" in result


def test_format_bus_option():
    # Available bus
    opt_avail = TravelOption(
        id="BUS_SETC_2030",
        mode=TravelMode.BUS,
        operator_name="SETC - AC Sleeper",
        source="Chennai",
        destination="Madurai",
        travel_date="2026-11-08",
        departure_time="20:30",
        arrival_time="04:30",
        travel_class="AC Sleeper",
        status_type=SeatStatusType.AVAILABLE,
        status_detail="AVAILABLE 8",
        available_seats=8,
        fare=680.0,
    )
    res_avail = format_bus_option(opt_avail)
    assert "🟢" in res_avail
    assert "8 seats left" in res_avail
    assert "₹680" in res_avail

    # Sold out bus
    opt_sold = TravelOption(
        id="BUS_KPN_2100",
        mode=TravelMode.BUS,
        operator_name="KPN Travels",
        source="Chennai",
        destination="Madurai",
        travel_date="2026-11-08",
        departure_time="21:00",
        arrival_time="05:30",
        travel_class="AC Sleeper",
        status_type=SeatStatusType.NOT_AVAILABLE,
        status_detail="Sold Out",
        available_seats=0,
        fare=950.0,
    )
    res_sold = format_bus_option(opt_sold)
    assert "🔴" in res_sold
    assert "Sold Out" in res_sold


def test_format_watch_overview():
    watch = Watch(
        id=1,
        chat_id=12345,
        source="Chennai",
        destination="Madurai",
        travel_date="2026-11-08",
        mode="both",
    )

    t_opt = TravelOption(
        id="TRAIN_12635_3A_GN",
        mode=TravelMode.TRAIN,
        operator_name="12635 - VAIGAI SF EXP",
        source="MS",
        destination="MDU",
        travel_date="2026-11-08",
        departure_time="13:50",
        arrival_time="21:30",
        travel_class="3A",
        status_type=SeatStatusType.AVAILABLE,
        status_detail="AVAILABLE 14",
        available_seats=14,
        fare=850.0,
    )

    b_govt = TravelOption(
        id="BUS_SETC_2030",
        mode=TravelMode.BUS,
        operator_name="SETC - AC Sleeper",
        source="Chennai",
        destination="Madurai",
        travel_date="2026-11-08",
        departure_time="20:30",
        arrival_time="04:30",
        travel_class="AC Sleeper",
        status_type=SeatStatusType.AVAILABLE,
        status_detail="AVAILABLE 12",
        available_seats=12,
        fare=680.0,
        extra={"bus_type": "govt"},
    )

    b_pvt = TravelOption(
        id="BUS_INTRCITY_2200",
        mode=TravelMode.BUS,
        operator_name="IntrCity SmartBus",
        source="Chennai",
        destination="Madurai",
        travel_date="2026-11-08",
        departure_time="22:00",
        arrival_time="06:00",
        travel_class="AC Sleeper (2+1)",
        status_type=SeatStatusType.AVAILABLE,
        status_detail="AVAILABLE 6",
        available_seats=6,
        fare=920.0,
        extra={"bus_type": "private"},
    )

    overview = format_watch_overview(watch, [t_opt, b_govt, b_pvt])
    assert "TRAINS" in overview
    assert "12635 - VAIGAI SF EXP" in overview
    assert "🟢" in overview
    assert "GOVERNMENT BUSES (TNSTC / SETC)" in overview
    assert "SETC - AC Sleeper" in overview
    assert "PRIVATE BUSES" in overview
    assert "IntrCity SmartBus" in overview
