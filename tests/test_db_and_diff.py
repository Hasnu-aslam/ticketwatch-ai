import tempfile
import pytest

from app.db import Database, Snapshot, Watch
from app.diff import DiffChangeType, DiffEngine
from app.models import SeatStatusType, TravelMode, TravelOption


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db = Database(tmp.name)
        yield db


def test_db_watch_lifecycle(temp_db: Database):
    # 1. Create watch
    watch = temp_db.create_watch(
        chat_id=12345678,
        source="Chennai",
        destination="Madurai",
        travel_date="2026-11-08",
        mode="both",
        filters={"classes": ["3A", "SL"], "max_price": 1000},
    )
    assert watch.id is not None
    assert watch.chat_id == 12345678
    assert watch.filters["classes"] == ["3A", "SL"]
    assert watch.is_active is True

    # 2. Query active watches
    active_watches = temp_db.get_active_watches()
    assert len(active_watches) == 1
    assert active_watches[0].id == watch.id

    # 3. Query user watches
    user_watches = temp_db.get_user_watches(12345678)
    assert len(user_watches) == 1

    # 4. Snapshots upsert and retrieve
    snaps = [
        Snapshot(
            watch_id=watch.id,
            item_id="TRAIN_12635_3A_GN",
            status_type=SeatStatusType.WAITLIST.value,
            status_detail="PQWL12/WL7",
            available_seats=None,
            waitlist_number=7,
            fare=850,
            booking_url="https://test.com",
            updated_at="2026-10-29T18:00:00",
        )
    ]
    temp_db.upsert_snapshots(watch.id, snaps)

    retrieved = temp_db.get_latest_snapshots(watch.id)
    assert "TRAIN_12635_3A_GN" in retrieved
    assert retrieved["TRAIN_12635_3A_GN"].waitlist_number == 7

    # 5. Deactivate watch
    assert temp_db.deactivate_watch(watch.id, chat_id=12345678) is True
    assert len(temp_db.get_active_watches()) == 0


def test_diff_engine_seat_opened():
    diff_engine = DiffEngine()
    watch = Watch(
        id=1,
        chat_id=100,
        mode="both",
        source="Chennai",
        destination="Madurai",
        travel_date="2026-11-08",
        created_at="2026-10-29",
    )

    prev_snaps = {
        "TRAIN_12635_3A_GN": Snapshot(
            watch_id=1,
            item_id="TRAIN_12635_3A_GN",
            status_type=SeatStatusType.WAITLIST.value,
            status_detail="PQWL25/WL15",
            available_seats=None,
            waitlist_number=15,
            fare=850,
            updated_at="2026-10-29",
        )
    }

    # Now seat opened!
    current_options = [
        TravelOption(
            id="TRAIN_12635_3A_GN",
            mode=TravelMode.TRAIN,
            operator_name="12635 - VAIGAI SF EXP",
            source="MAS",
            destination="MDU",
            travel_date="2026-11-08",
            departure_time="13:50",
            arrival_time="21:15",
            travel_class="3A",
            status_type=SeatStatusType.AVAILABLE,
            status_detail="AVAILABLE 4",
            available_seats=4,
            fare=850,
        )
    ]

    diffs = diff_engine.compute_diffs(watch, current_options, prev_snaps)
    assert len(diffs) == 1
    assert diffs[0].change_type == DiffChangeType.SEAT_OPENED
    assert diffs[0].is_urgent is True
    assert "SEAT OPENED" in diffs[0].message


def test_diff_engine_minor_wl_ignored():
    diff_engine = DiffEngine()
    watch = Watch(
        id=1,
        chat_id=100,
        mode="both",
        source="Chennai",
        destination="Madurai",
        travel_date="2026-11-08",
        created_at="2026-10-29",
    )

    prev_snaps = {
        "TRAIN_12635_3A_GN": Snapshot(
            watch_id=1,
            item_id="TRAIN_12635_3A_GN",
            status_type=SeatStatusType.WAITLIST.value,
            status_detail="WL 85",
            available_seats=None,
            waitlist_number=85,
            updated_at="2026-10-29",
        )
    }

    # Minor change: WL 85 -> WL 84 (should be IGNORED to prevent notification spam)
    current_options = [
        TravelOption(
            id="TRAIN_12635_3A_GN",
            mode=TravelMode.TRAIN,
            operator_name="12635 - VAIGAI SF EXP",
            source="MAS",
            destination="MDU",
            travel_date="2026-11-08",
            departure_time="13:50",
            arrival_time="21:15",
            travel_class="3A",
            status_type=SeatStatusType.WAITLIST,
            status_detail="WL 84",
            waitlist_number=84,
        )
    ]

    diffs = diff_engine.compute_diffs(watch, current_options, prev_snaps)
    assert len(diffs) == 0  # No spam!


def test_diff_engine_price_drop():
    diff_engine = DiffEngine()
    watch = Watch(
        id=1,
        chat_id=100,
        mode="bus",
        source="Chennai",
        destination="Madurai",
        travel_date="2026-11-08",
        created_at="2026-10-29",
    )

    prev_snaps = {
        "BUS_KPN_2100_0": Snapshot(
            watch_id=1,
            item_id="BUS_KPN_2100_0",
            status_type=SeatStatusType.AVAILABLE.value,
            status_detail="AVAILABLE (10 seats)",
            available_seats=10,
            fare=1250,
            updated_at="2026-10-29",
        )
    }

    # Price dropped by ₹250 (from 1250 to 1000)
    current_options = [
        TravelOption(
            id="BUS_KPN_2100_0",
            mode=TravelMode.BUS,
            operator_name="KPN Travels",
            source="Chennai",
            destination="Madurai",
            travel_date="2026-11-08",
            departure_time="21:00",
            arrival_time="05:30",
            travel_class="AC Sleeper",
            status_type=SeatStatusType.AVAILABLE,
            status_detail="AVAILABLE (10 seats)",
            available_seats=10,
            fare=1000,
        )
    ]

    diffs = diff_engine.compute_diffs(watch, current_options, prev_snaps)
    assert len(diffs) == 1
    assert diffs[0].change_type == DiffChangeType.PRICE_DROPPED
    assert "₹250" in diffs[0].message
