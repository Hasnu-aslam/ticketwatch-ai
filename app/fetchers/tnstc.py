"""
TNSTC & SETC Bus Fetcher
Fetches live, genuine government bus availability from Tamil Nadu State Transport Corporation (tnstc.in).
Uses live OTRSOnline autocomplete place resolution and direct jqreq search.
Zero synthetic/fake data generation — only authentic live availability.
"""

import logging
import re
from typing import Optional

import httpx

from app.fetchers.base import BaseFetcher
from app.models import (
    SearchQuery,
    SeatStatusType,
    TravelMode,
    TravelOption,
)

logger = logging.getLogger(__name__)

# Pre-seeded canonical TNSTC place IDs and codes (ID, Code, Full Name)
DEFAULT_TNSTC_PLACES: dict[str, tuple[str, str, str]] = {
    "chennai": ("1392", "KCB", "CHENNAI-KILAMBAKKAM-KCBT"),
    "madras": ("1392", "KCB", "CHENNAI-KILAMBAKKAM-KCBT"),
    "thanjavur": ("434", "THA", "THANJAVUR"),
    "tanjore": ("434", "THA", "THANJAVUR"),
    "madurai": ("75", "MAD", "MADURAI"),
    "coimbatore": ("114", "COI", "COIMBATORE"),
    "trichy": ("74", "TRI", "TRICHY"),
    "tiruchirappalli": ("74", "TRI", "TRICHY"),
    "kumbakonam": ("431", "KUM", "KUMBAKONAM"),
    "salem": ("416", "SAL", "SALEM"),
    "tirunelveli": ("77", "TIU", "TIRUNELVELI"),
    "dindigul": ("482", "DIN", "DINDIGUL"),
    "erode": ("65", "ED", "ERODE"),
    "nagercoil": ("91", "NCJ", "NAGERCOIL"),
    "kanyakumari": ("90", "CAPE", "KANYAKUMARI"),
    "bangalore": ("1", "SBC", "BENGALURU"),
    "bengaluru": ("1", "SBC", "BENGALURU"),
    "vellore": ("70", "VEL", "VELLORE"),
    "hosur": ("66", "HOS", "HOSUR"),
    "tuticorin": ("88", "TUT", "TUTICORIN"),
}


class TNSTCFetcher(BaseFetcher):
    """
    Live Tamil Nadu State Transport Corporation (TNSTC / SETC) fetcher.
    Queries the official portal (tnstc.in) directly for genuine seat counts.
    """

    BASE_URL = "https://www.tnstc.in"
    OTRS_URL = "https://www.tnstc.in/OTRSOnline/"
    API_URL = "https://www.tnstc.in/OTRSOnline/jqreq.do"

    def __init__(self, timeout: float = 18.0):
        self.timeout = timeout
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://www.tnstc.in/OTRSOnline/",
            "Origin": "https://www.tnstc.in",
        }
        self._place_cache: dict[str, tuple[str, str, str]] = dict(DEFAULT_TNSTC_PLACES)

    async def _resolve_place(
        self, client: httpx.AsyncClient, city_name: str, is_from: bool = True
    ) -> Optional[tuple[str, str, str]]:
        """
        Resolves city string to (place_id, place_code, place_name).
        Checks local pre-seeded cache first; queries live TNSTC autocomplete if missing.
        """
        lower = city_name.strip().lower()

        # Check cache
        if lower in self._place_cache:
            return self._place_cache[lower]

        for k, v in self._place_cache.items():
            if k in lower or lower in k:
                return v

        # Live dynamic autocomplete resolution
        try:
            action = "LoadFromPlaceList" if is_from else "LoadTOPlaceList"
            param = "matchStartPlace" if is_from else "matchEndPlace"
            payload = f"hiddenAction={action}&{param}={lower}"

            resp = await client.post(self.API_URL, data=payload, headers=self.headers)
            if resp.status_code == 200 and resp.text.strip():
                # Format: ID:CODE:NAME^ID2:CODE2:NAME2^...
                entries = resp.text.split("^")
                for entry in entries:
                    parts = entry.split(":")
                    if len(parts) >= 3:
                        res = (parts[0].strip(), parts[1].strip(), parts[2].strip())
                        self._place_cache[lower] = res
                        return res
        except Exception as e:
            logger.debug("TNSTC autocomplete lookup failed for '%s': %s", city_name, e)

        return None

    async def fetch(self, query: SearchQuery) -> list[TravelOption]:
        """
        Queries tnstc.in live for government buses.
        Returns empty list if unserviceable or offline — never hallucinates fake data.
        """
        # Format travel date to DD/MM/YYYY
        date_parts = query.travel_date.split("-")
        if len(date_parts) == 3:
            dd_mm_yyyy = f"{date_parts[2]}/{date_parts[1]}/{date_parts[0]}"
        else:
            dd_mm_yyyy = query.travel_date

        try:
            async with httpx.AsyncClient(
                headers=self.headers, timeout=self.timeout, verify=False, follow_redirects=True
            ) as client:
                # 1. Establish session
                await client.get(self.OTRS_URL)

                # 2. Resolve origin & destination
                src_info = await self._resolve_place(client, query.source, is_from=True)
                dst_info = await self._resolve_place(client, query.destination, is_from=False)

                if not src_info or not dst_info:
                    logger.warning("Could not resolve TNSTC place codes for %s -> %s", query.source, query.destination)
                    return []

                src_id, src_code, src_name = src_info
                dst_id, dst_code, dst_name = dst_info

                # 3. Submit real search request
                form_data = {
                    "hiddenAction": "SearchService",
                    "hiddenMaxNoOfPassengers": "16",
                    "selectStartPlace": src_code,
                    "selectEndPlace": dst_code,
                    "matchStartPlace": src_name,
                    "matchEndPlace": dst_name,
                    "hiddenUserType": "G",
                    "txtdeptDateOtrip": dd_mm_yyyy,
                    "txtJourneyDate": dd_mm_yyyy,
                    "hiddenOnwardJourneyDate": dd_mm_yyyy,
                    "hiddenStartPlaceID": src_id,
                    "hiddenEndPlaceID": dst_id,
                    "txtStartPlaceCode": src_code,
                    "txtEndPlaceCode": dst_code,
                    "hiddenStartPlaceName": src_name,
                    "hiddenEndPlaceName": dst_name,
                    "checkSingleLady": "N",
                    "txtReturnDate": "DD/MM/YYYY",
                }

                resp = await client.post(self.API_URL, data=form_data)
                if resp.status_code == 200:
                    return self._parse_tnstc_html(resp.text, query)

        except Exception as e:
            logger.error("TNSTC live fetch error for %s -> %s: %s", query.source, query.destination, e)

        return []

    def _parse_tnstc_html(self, html: str, query: SearchQuery) -> list[TravelOption]:
        """
        Extracts real bus cards from TNSTC response HTML.
        """
        bus_items = re.findall(r'<div class="bus-item">(.*?)</div>\s*</div>\s*</div>', html, re.DOTALL)
        options: list[TravelOption] = []

        for idx, item in enumerate(bus_items):
            op_m = re.search(r'operator-name">([^<]+)</span>', item)
            type_m = re.search(r'<span class="text-1 text-muted d-block">([^<]+)</span>', item)
            service_m = re.search(
                r'loadTripDetails\([^,]+,[^,]+,[^,]+,[^,]+,[^,]+,[^,]+,[^,]+,[^,]+,[^,]+,[^,]+,([^,\'\"]+)',
                item,
            )
            dep_m = re.search(r'<span class="text-4 text-dark">([^<]+)</span>', item)
            arr_m = re.search(r'<span class="text-5 text-dark">([^<]+)</span>', item)
            dur_m = re.search(r'class="text-3 duration">([^<]+)</span>', item)

            # Strip HTML comments to avoid parsing commented template values
            clean_item = re.sub(r'<!--.*?-->', '', item, flags=re.DOTALL)
            price_m = re.search(r'class="[^"]*price[^"]*">\s*(?:Rs)?\s*([0-9]+)', clean_item)
            seats_m = re.search(r'([0-9]+)\s+Seats?\s+Available', clean_item, re.IGNORECASE)
            is_full = "<span>Full</span>" in item or "buttonF" in item

            op = op_m.group(1).strip() if op_m else "TNSTC"
            btype = type_m.group(1).strip() if type_m else "Deluxe"
            service_code = service_m.group(1).strip() if service_m else f"{idx}"
            dep = dep_m.group(1).strip() if dep_m else "00:00"
            arr = arr_m.group(1).strip() if arr_m else "00:00"
            dur = dur_m.group(1).strip() if dur_m else ""
            fare = float(price_m.group(1)) if price_m else None

            if is_full:
                seats = 0
                status_type = SeatStatusType.NOT_AVAILABLE
                status_detail = "SOLD OUT"
            elif seats_m:
                seats = int(seats_m.group(1))
                status_type = SeatStatusType.AVAILABLE if seats > 0 else SeatStatusType.NOT_AVAILABLE
                status_detail = f"AVAILABLE ({seats})" if seats > 0 else "SOLD OUT"
            else:
                seats = None
                status_type = SeatStatusType.UNKNOWN
                status_detail = "Check Portal"

            opt = TravelOption(
                id=f"TNSTC_{service_code}_{dep.replace(':', '')}_{idx}",
                mode=TravelMode.BUS,
                operator_name=f"{op} - {btype}",
                source=query.source.title(),
                destination=query.destination.title(),
                travel_date=query.travel_date,
                departure_time=dep,
                arrival_time=arr,
                duration=dur,
                travel_class=btype,
                status_type=status_type,
                status_detail=status_detail,
                available_seats=seats,
                fare=fare,
                booking_url="https://www.tnstc.in/OTRSOnline/",
                extra={"operator": op, "bus_type": "govt", "service_code": service_code},
            )
            options.append(opt)

        return options
