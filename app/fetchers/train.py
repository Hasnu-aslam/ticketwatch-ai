import logging
import secrets
from datetime import datetime
from typing import Optional
import httpx

from app.fetchers.base import BaseFetcher
from app.models import (
    SearchQuery,
    SeatStatusType,
    TravelMode,
    TravelOption,
    parse_seat_status,
)
from app.station_resolver import resolve_station_code

logger = logging.getLogger(__name__)


class ConfirmTktTrainFetcher(BaseFetcher):
    """
    Live Indian Railways train availability fetcher using ConfirmTkt backend.
    """

    BASE_URL = "https://securedapi.confirmtkt.com/api/platform/trainbooking/tatwnstns"

    def __init__(self, timeout: float = 12.0):
        self.timeout = timeout
        self.headers = {
            "User-Agent": "okhttp/4.9.2",
            "Host": "securedapi.confirmtkt.com",
            "Connection": "Keep-Alive",
            "Accept": "application/json",
        }

    @staticmethod
    def _format_date(date_str: str) -> str:
        """
        Converts 'YYYY-MM-DD' to 'DD-MM-YYYY'.
        If already 'DD-MM-YYYY', returns as-is.
        """
        date_str = date_str.strip()
        if "-" in date_str:
            parts = date_str.split("-")
            if len(parts[0]) == 4:  # YYYY-MM-DD
                return f"{parts[2]}-{parts[1]}-{parts[0]}"
        return date_str

    async def fetch(self, query: SearchQuery) -> list[TravelOption]:
        """
        Fetches live train availability for the given route and date.
        """
        src_code = resolve_station_code(query.source)
        dest_code = resolve_station_code(query.destination)
        doj_formatted = self._format_date(query.travel_date)
        quota = (query.quota or "GN").upper()

        params = {
            "fromStnCode": src_code,
            "destStnCode": dest_code,
            "doj": doj_formatted,
            "quota": quota,
            "token": secrets.token_hex(32),
            "session": secrets.token_hex(16),
            "appVersion": "397",
        }

        options: list[TravelOption] = []

        try:
            async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
                resp = await client.get(self.BASE_URL, params=params)
                resp.raise_for_status()
                data = resp.json()

            train_list = data.get("trainBtwnStnsList") or []
            logger.info("Found %d trains for %s -> %s on %s", len(train_list), src_code, dest_code, doj_formatted)

            for t in train_list:
                train_num = str(t.get("trainNumber", "")).strip()
                train_name = t.get("trainName", "Express").strip()
                dep_time = t.get("departureTime", "00:00")
                arr_time = t.get("arrivalTime", "00:00")
                duration = t.get("duration", "")
                actual_from = t.get("fromStnCode", src_code)
                actual_to = t.get("toStnCode", dest_code)

                # Availability cache contains class-by-class data
                avail_cache = t.get("avaiblitycache") or {}
                if not isinstance(avail_cache, dict):
                    continue

                for cls_name, cls_info in avail_cache.items():
                    if not isinstance(cls_info, dict):
                        continue

                    raw_status = cls_info.get("Availability") or ""
                    status_type, seats, wl = parse_seat_status(raw_status)

                    # Extract fare if present
                    fare_val = cls_info.get("TotalFare")
                    fare = float(fare_val) if fare_val is not None else None

                    # Confirmation prediction percentage if available
                    pred_pct = cls_info.get("PredictionPercentage")
                    parsed_pred: Optional[float] = None
                    if pred_pct is not None:
                        try:
                            parsed_pred = float(pred_pct)
                        except (ValueError, TypeError):
                            parsed_pred = None

                    # Create unique stable identifier for this train class and quota
                    option_id = f"TRAIN_{train_num}_{cls_name}_{quota}"

                    option = TravelOption(
                        id=option_id,
                        mode=TravelMode.TRAIN,
                        operator_name=f"{train_num} - {train_name}",
                        train_number=train_num,
                        source=actual_from,
                        destination=actual_to,
                        travel_date=query.travel_date,
                        departure_time=dep_time,
                        arrival_time=arr_time,
                        duration=duration,
                        travel_class=cls_name,
                        quota=quota,
                        status_type=status_type,
                        status_detail=raw_status,
                        available_seats=seats,
                        waitlist_number=wl,
                        fare=fare,
                        prediction_percentage=parsed_pred,
                        booking_url=f"https://www.confirmtkt.com/train-running-status/{train_num}",
                        extra={
                            "prediction_percentage": parsed_pred,
                            "train_name": train_name,
                            "from_station_name": t.get("fromStnName"),
                            "to_station_name": t.get("toStnName"),
                        },
                    )
                    options.append(option)

        except Exception as e:
            logger.error("Failed to fetch train availability for %s -> %s: %s", src_code, dest_code, e)
            raise

        return options
