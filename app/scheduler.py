import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Awaitable, Callable, Optional

from app.db import Database, Snapshot, Watch
from app.diff import DiffEngine, DiffResult
from app.fetchers.private_bus import PrivateBusFetcher
from app.fetchers.tnstc import TNSTCFetcher
from app.fetchers.train import ConfirmTktTrainFetcher
from app.models import SearchQuery, TravelMode, TravelOption

logger = logging.getLogger(__name__)

NotifierCallback = Callable[[Watch, list[DiffResult]], Awaitable[None]]


class WatchScheduler:
    """
    Adaptive Tiered Watch Scheduler:
    - Buses: Scanned 4 times a day (08:30, 13:30, 18:30, 22:30) via TNSTC & Playwright Private Buses.
    - Trains: Scanned every 2.5 hours regular, bursting during Tatkal (T-1 day at 10 AM & 11 AM) and Chart Prep (T-0 day).
    - Night window (23:45 to 06:00): System rests to match IRCTC maintenance.
    - Instant checks: Supported on-demand via Telegram /check.
    """

    # Scheduled daily hours for bus sweeps (4 times a day)
    BUS_SWEEP_WINDOWS = [
        (time(8, 25), time(8, 45)),   # ~08:30 AM
        (time(13, 25), time(13, 45)), # ~01:30 PM
        (time(18, 25), time(18, 45)), # ~06:30 PM
        (time(22, 25), time(22, 45)), # ~10:30 PM
    ]

    def __init__(
        self,
        db: Database,
        notifier: Optional[NotifierCallback] = None,
        train_fetcher: Optional[ConfirmTktTrainFetcher] = None,
        tnstc_fetcher: Optional[TNSTCFetcher] = None,
        private_bus_fetcher: Optional[PrivateBusFetcher] = None,
    ):
        self.db = db
        self.notifier = notifier
        self.train_fetcher = train_fetcher or ConfirmTktTrainFetcher()
        self.tnstc_fetcher = tnstc_fetcher or TNSTCFetcher()
        self.private_bus_fetcher = private_bus_fetcher or PrivateBusFetcher()
        self.diff_engine = DiffEngine()
        self._running = False
        self._last_bus_sweep_date: Optional[str] = None
        self._bus_sweeps_completed_today: set[int] = set()

    def should_run_bus_sweep_now(self, force: bool = False) -> bool:
        """
        Determines if current time aligns with one of the 4 daily bus sweep slots.
        """
        if force:
            return True

        now = datetime.now()
        cur_date_str = now.strftime("%Y-%m-%d")
        if self._last_bus_sweep_date != cur_date_str:
            self._last_bus_sweep_date = cur_date_str
            self._bus_sweeps_completed_today.clear()

        cur_time = now.time()
        for idx, (start_t, end_t) in enumerate(self.BUS_SWEEP_WINDOWS):
            if start_t <= cur_time <= end_t:
                if idx not in self._bus_sweeps_completed_today:
                    self._bus_sweeps_completed_today.add(idx)
                    return True

        return False

    def is_tatkal_burst_window(self, travel_date_str: str) -> bool:
        """
        Tatkal window is active ONLY on the day prior to journey date (T-1):
        - AC Tatkal: 09:59 to 10:06 AM
        - Non-AC Tatkal: 10:59 to 11:06 AM
        """
        now = datetime.now()
        try:
            journey_date = datetime.strptime(travel_date_str, "%Y-%m-%d").date()
        except Exception:
            return False

        # Only on T-1 day
        if journey_date - timedelta(days=1) != now.date():
            return False

        cur_time = now.time()
        ac_window = time(9, 59) <= cur_time <= time(10, 6)
        non_ac_window = time(10, 59) <= cur_time <= time(11, 6)
        return ac_window or non_ac_window

    def is_chart_prep_window(self, travel_date_str: str) -> bool:
        """
        Chart preparation window is active on journey date (T-0) between 4 hours before departure.
        """
        now = datetime.now()
        try:
            journey_date = datetime.strptime(travel_date_str, "%Y-%m-%d").date()
        except Exception:
            return False

        if journey_date == now.date():
            # Afternoon and evening chart preparation
            cur_time = now.time()
            if time(14, 0) <= cur_time <= time(20, 0):
                return True
        return False

    def is_night_sleep_window(self) -> bool:
        """
        Returns True during IRCTC shutdown and late night quiet hours (23:45 to 06:00).
        """
        cur_time = datetime.now().time()
        return cur_time >= time(23, 45) or cur_time < time(6, 0)

    async def check_watch(self, watch: Watch, force_bus: bool = False) -> list[DiffResult]:
        """
        Executes a targeted check for a single watch.
        """
        logger.info("Checking watch #%d: %s ➔ %s (%s)", watch.id, watch.source, watch.destination, watch.travel_date)

        filters = watch.filters or {}
        classes = filters.get("classes")
        max_price = filters.get("max_price")
        dep_after = filters.get("departure_after")
        dep_before = filters.get("departure_before")
        quota = filters.get("quota", "GN")

        query = SearchQuery(
            source=watch.source,
            destination=watch.destination,
            travel_date=watch.travel_date,
            classes=classes,
            max_price=max_price,
            departure_after=dep_after,
            departure_before=dep_before,
            quota=quota,
        )

        options: list[TravelOption] = []

        # 1. Fetch Trains (Direct HTTP)
        if watch.mode in ("train", "both"):
            try:
                train_opts = await self.train_fetcher.search(query)
                options.extend(train_opts)
            except Exception as e:
                logger.error("Error fetching trains for watch #%d: %s", watch.id, e)

        # 2. Check previous snapshots (to know if this is the initial check)
        previous_snapshots = self.db.get_latest_snapshots(watch.id)
        is_initial = len(previous_snapshots) == 0

        # 3. Fetch Buses (TNSTC government + Private Buses)
        # Always run buses if force_bus=True, or on initial watch scan, or during 4x daily sweep windows
        run_bus = self.should_run_bus_sweep_now(force=force_bus) or is_initial
        if watch.mode in ("bus", "both") and run_bus:
            # 3a. Fetch TNSTC / SETC government buses
            try:
                tnstc_opts = await self.tnstc_fetcher.search(query)
                options.extend(tnstc_opts)
                logger.info("Found %d TNSTC/SETC options", len(tnstc_opts))
            except Exception as e:
                logger.error("Error fetching TNSTC for watch #%d: %s", watch.id, e)

            # 3b. Fetch Private Buses via Playwright / RedBus
            try:
                pvt_opts = await self.private_bus_fetcher.search(query)
                options.extend(pvt_opts)
                logger.info("Found %d private bus options", len(pvt_opts))
            except Exception as e:
                logger.error("Error fetching private buses for watch #%d: %s", watch.id, e)

        # 4. Compute actionable diffs
        diffs = self.diff_engine.compute_diffs(
            watch=watch,
            current_options=options,
            previous_snapshots=previous_snapshots,
            is_initial_check=is_initial,
        )

        # 5. Save updated snapshots
        new_snapshots = [
            Snapshot(
                watch_id=watch.id,
                item_id=opt.id,
                status_type=opt.status_type.value,
                status_detail=opt.status_detail,
                available_seats=opt.available_seats,
                waitlist_number=opt.waitlist_number,
                fare=opt.fare,
                booking_url=opt.booking_url,
                updated_at=datetime.now(timezone.utc).isoformat(),
                raw_payload=opt.model_dump_json(),
            )
            for opt in options
        ]
        self.db.upsert_snapshots(watch.id, new_snapshots)
        self.db.update_watch_checked_time(watch.id)

        # 6. Notify if diffs found
        if diffs and self.notifier:
            try:
                await self.notifier(watch, diffs)
                self.db.update_watch_alerted_time(watch.id)
            except Exception as e:
                logger.error("Error delivering notification for watch #%d: %s", watch.id, e)

        return diffs

    async def check_watch_now(self, watch_id: int) -> tuple[Optional[Watch], list[DiffResult]]:
        """
        Allows users to force an instant, comprehensive check (including buses) via /check.
        """
        watch = self.db.get_watch(watch_id)
        if not watch:
            return None, []
        diffs = await self.check_watch(watch, force_bus=True)
        return watch, diffs

    def get_latest_options(self, watch_id: int) -> list[TravelOption]:
        """
        Reconstructs the latest TravelOption objects from the DB snapshot store.
        """
        snaps = self.db.get_latest_snapshots(watch_id)
        options: list[TravelOption] = []
        for s in snaps.values():
            if s.raw_payload:
                try:
                    options.append(TravelOption.model_validate_json(s.raw_payload))
                except Exception:
                    pass
        return options

    async def run_once(self) -> dict[int, list[DiffResult]]:
        active_watches = self.db.get_active_watches()
        results: dict[int, list[DiffResult]] = {}

        for watch in active_watches:
            try:
                diffs = await self.check_watch(watch)
                results[watch.id] = diffs
            except Exception as e:
                logger.error("Failed checking watch #%d: %s", watch.id, e)

        return results

    async def start(self, default_interval_sec: int = 9000):
        """
        Main scheduler loop with adaptive intervals:
        - 25s during Tatkal window (T-1 day at 10 AM / 11 AM)
        - 300s during Chart Prep window
        - 2.5 hours regular daytime
        - Paused during late night (23:45 to 06:00)
        """
        self._running = True
        logger.info("WatchScheduler started with adaptive tiered intervals.")

        while self._running:
            active_watches = self.db.get_active_watches()

            if not self.is_night_sleep_window():
                await self.run_once()
            else:
                logger.info("🌙 Night quiet hours (23:45–06:00). Pausing poller until morning.")

            # Calculate optimal next sleep interval
            sleep_sec = default_interval_sec  # 2.5 hours default (9000s)

            # Check if ANY active watch is in Tatkal or Chart Prep window
            for w in active_watches:
                if self.is_tatkal_burst_window(w.travel_date):
                    sleep_sec = 25
                    logger.info("⚡ Tatkal Window Active for Watch #%d! Burst polling every 25s", w.id)
                    break
                elif self.is_chart_prep_window(w.travel_date):
                    sleep_sec = min(sleep_sec, 300)

            await asyncio.sleep(sleep_sec)

    def stop(self):
        self._running = False
