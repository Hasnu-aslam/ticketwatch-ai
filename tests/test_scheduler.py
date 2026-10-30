from datetime import datetime, timedelta, timezone
import tempfile
import pytest

from app.db import Database, Watch
from app.diff import DiffChangeType, DiffResult
from app.fetchers.base import BaseFetcher
from app.models import (
    SearchQuery,
    SeatStatusType,
    TravelMode,
    TravelOption,
)
from app.scheduler import WatchScheduler


class MockTrainFetcher(BaseFetcher):
    def __init__(self, initial_status: str, initial_type: SeatStatusType, seats: int = None, wl: int = None):
        self.status_detail = initial_status
        self.status_type = initial_type
        self.available_seats = seats
        self.waitlist_number = wl

    async def fetch(self, query: SearchQuery) -> list[TravelOption]:
        return [
            TravelOption(
                id="TRAIN_12635_3A_GN",
                mode=TravelMode.TRAIN,
                operator_name="12635 - VAIGAI SF EXP",
                source=query.source,
                destination=query.destination,
                travel_date=query.travel_date,
                departure_time="13:50",
                arrival_time="21:15",
                travel_class="3A",
                status_type=self.status_type,
                status_detail=self.status_detail,
                available_seats=self.available_seats,
                waitlist_number=self.waitlist_number,
                fare=850,
            )
        ]


@pytest.mark.asyncio
async def test_scheduler_end_to_end():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db = Database(tmp.name)

        # 1. Create a watch
        watch = db.create_watch(
            chat_id=999,
            source="Chennai",
            destination="Madurai",
            travel_date="2026-11-08",
            mode="train",
            filters={"classes": ["3A"]},
        )

        # Initially waitlisted
        mock_fetcher = MockTrainFetcher("PQWL20/WL15", SeatStatusType.WAITLIST, wl=15)
        notifications_received = []

        async def fake_notifier(w: Watch, diffs: list[DiffResult]):
            notifications_received.append((w.id, diffs))

        scheduler = WatchScheduler(
            db=db,
            notifier=fake_notifier,
            train_fetcher=mock_fetcher,
        )

        # First run: initial snapshot is stored
        await scheduler.check_watch(watch)
        snaps = db.get_latest_snapshots(watch.id)
        assert "TRAIN_12635_3A_GN" in snaps
        assert snaps["TRAIN_12635_3A_GN"].status_type == SeatStatusType.WAITLIST.value
        assert len(notifications_received) == 0  # No alert for initial waitlist

        # Second run: Seat opens up! (WL -> AVAILABLE 5)
        mock_fetcher.status_type = SeatStatusType.AVAILABLE
        mock_fetcher.status_detail = "AVAILABLE 5"
        mock_fetcher.available_seats = 5
        mock_fetcher.waitlist_number = None

        diffs = await scheduler.check_watch(watch)
        assert len(diffs) == 1
        assert diffs[0].change_type == DiffChangeType.SEAT_OPENED
        assert len(notifications_received) == 1
        assert notifications_received[0][0] == watch.id

        # Verify DB updated
        updated_watch = db.get_watch(watch.id)
        assert updated_watch.last_checked_at is not None
        assert updated_watch.last_alerted_at is not None
        updated_snaps = db.get_latest_snapshots(watch.id)
        assert updated_snaps["TRAIN_12635_3A_GN"].status_type == SeatStatusType.AVAILABLE.value

        # Test on-demand manual check (/check)
        w_obj, diffs_now = await scheduler.check_watch_now(watch.id)
        assert w_obj is not None
        assert w_obj.id == watch.id


def test_adaptive_polling_helpers():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db = Database(tmp.name)
        scheduler = WatchScheduler(db=db)

        # Force bus sweep check
        assert scheduler.should_run_bus_sweep_now(force=True) is True

        # Test tatkal window logic on non-T-1 date (e.g. 10 days in future)
        future_date = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
        assert scheduler.is_tatkal_burst_window(future_date) is False

