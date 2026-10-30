from datetime import datetime, timedelta
import pytest

from app.fetchers.base import BaseFetcher
from app.fetchers.bus import BusFetcher
from app.fetchers.train import ConfirmTktTrainFetcher
from app.models import (
    SearchQuery,
    SeatStatusType,
    TravelMode,
    TravelOption,
    parse_seat_status,
)
from app.station_resolver import resolve_all_station_codes, resolve_station_code


def test_parse_seat_status():
    # Available
    status, seats, wl = parse_seat_status("AVAILABLE 14")
    assert status == SeatStatusType.AVAILABLE
    assert seats == 14
    assert wl is None

    status, seats, wl = parse_seat_status("CURR_AVBL-0005")
    assert status == SeatStatusType.AVAILABLE
    assert seats == 5

    # RAC
    status, seats, wl = parse_seat_status("RAC 23")
    assert status == SeatStatusType.RAC
    assert seats == 23

    # Waitlist (single and dual counter)
    status, seats, wl = parse_seat_status("PQWL12/WL7")
    assert status == SeatStatusType.WAITLIST
    assert wl == 7

    status, seats, wl = parse_seat_status("GNWL 85/WL 40")
    assert status == SeatStatusType.WAITLIST
    assert wl == 40

    status, seats, wl = parse_seat_status("WL 12")
    assert status == SeatStatusType.WAITLIST
    assert wl == 12

    # Regret & Unavailable
    status, _, _ = parse_seat_status("REGRET")
    assert status == SeatStatusType.REGRET

    status, _, _ = parse_seat_status("TRAIN CANCELLED")
    assert status == SeatStatusType.NOT_AVAILABLE


def test_station_resolver():
    assert resolve_station_code("Chennai") == "MAS"
    assert resolve_station_code("Madurai") == "MDU"
    assert resolve_station_code("bangalore") == "SBC"
    assert resolve_station_code("Delhi") == "NDLS"
    assert resolve_station_code("Kochi") == "ERS"
    assert resolve_station_code("MAS") == "MAS"
    assert resolve_station_code("MDU") == "MDU"

    all_chennai = resolve_all_station_codes("Chennai")
    assert "MAS" in all_chennai
    assert "MS" in all_chennai


def test_apply_filters():
    opts = [
        TravelOption(
            id="1",
            mode=TravelMode.TRAIN,
            operator_name="Train A",
            source="MAS",
            destination="MDU",
            travel_date="2026-11-10",
            departure_time="18:30",
            arrival_time="02:00",
            travel_class="SL",
            status_type=SeatStatusType.AVAILABLE,
            status_detail="AVAILABLE 10",
            fare=350,
        ),
        TravelOption(
            id="2",
            mode=TravelMode.TRAIN,
            operator_name="Train B",
            source="MAS",
            destination="MDU",
            travel_date="2026-11-10",
            departure_time="21:15",
            arrival_time="05:30",
            travel_class="3A",
            status_type=SeatStatusType.AVAILABLE,
            status_detail="AVAILABLE 5",
            fare=920,
        ),
        TravelOption(
            id="3",
            mode=TravelMode.BUS,
            operator_name="Bus C",
            source="Chennai",
            destination="Madurai",
            travel_date="2026-11-10",
            departure_time="22:00",
            arrival_time="06:00",
            travel_class="AC Sleeper",
            status_type=SeatStatusType.AVAILABLE,
            status_detail="AVAILABLE 8",
            fare=1200,
        ),
    ]

    # Test class filter
    q_class = SearchQuery(source="MAS", destination="MDU", travel_date="2026-11-10", classes=["3A"])
    res = BaseFetcher.apply_filters(opts, q_class)
    assert len(res) == 1
    assert res[0].travel_class == "3A"

    # Test max price filter
    q_price = SearchQuery(source="MAS", destination="MDU", travel_date="2026-11-10", max_price=1000)
    res = BaseFetcher.apply_filters(opts, q_price)
    assert len(res) == 2
    assert all(o.fare <= 1000 for o in res)

    # Test departure time filter (after 20:00)
    q_time = SearchQuery(source="MAS", destination="MDU", travel_date="2026-11-10", departure_after="20:00")
    res = BaseFetcher.apply_filters(opts, q_time)
    assert len(res) == 2
    assert all(o.departure_time >= "20:00" for o in res)


@pytest.mark.asyncio
async def test_bus_fetcher():
    fetcher = BusFetcher()
    query = SearchQuery(
        source="Chennai",
        destination="Madurai",
        travel_date="2026-11-10",
        classes=["AC Sleeper"],
        max_price=1500,
        departure_after="21:00",
    )
    results = await fetcher.search(query)
    assert len(results) > 0
    for r in results:
        assert r.mode == TravelMode.BUS
        assert "redbus.in" in (r.booking_url or "")
        assert r.fare <= 1500
        assert r.departure_time >= "21:00"


@pytest.mark.asyncio
async def test_live_train_fetcher():
    fetcher = ConfirmTktTrainFetcher()
    # Check 7 days in future to ensure trains run
    future_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    query = SearchQuery(
        source="Chennai",
        destination="Madurai",
        travel_date=future_date,
        classes=["3A", "SL"],
    )

    results = await fetcher.search(query)
    # ConfirmTkt should return train options for Chennai -> Madurai
    assert len(results) > 0
    sample = results[0]
    assert sample.mode == TravelMode.TRAIN
    assert sample.source in ("MAS", "MS", "TBM")
    assert sample.destination == "MDU"
    assert sample.travel_class in ("3A", "SL")
    assert sample.status_type in (
        SeatStatusType.AVAILABLE,
        SeatStatusType.RAC,
        SeatStatusType.WAITLIST,
        SeatStatusType.REGRET,
        SeatStatusType.NOT_AVAILABLE,
        SeatStatusType.UNKNOWN,
    )


@pytest.mark.asyncio
async def test_tnstc_fetcher():
    from app.fetchers.tnstc import TNSTCFetcher

    fetcher = TNSTCFetcher()
    query = SearchQuery(
        source="Chennai",
        destination="Madurai",
        travel_date="2026-11-08",
        max_price=800,
    )
    results = await fetcher.search(query)
    assert len(results) > 0
    for r in results:
        assert r.mode == TravelMode.BUS
        assert "TNSTC" in r.operator_name or "SETC" in r.operator_name
        assert r.fare <= 800
        assert "tnstc.in" in (r.booking_url or "")


@pytest.mark.asyncio
async def test_private_bus_fetcher():
    from app.fetchers.private_bus import PrivateBusFetcher

    fetcher = PrivateBusFetcher()
    query = SearchQuery(
        source="Chennai",
        destination="Madurai",
        travel_date="2026-11-08",
        classes=["AC Sleeper"],
        max_price=1600,
    )
    results = await fetcher.search(query)
    assert len(results) > 0
    for r in results:
        assert r.mode == TravelMode.BUS
        assert "redbus.in" in (r.booking_url or "")
        assert r.fare <= 1600

