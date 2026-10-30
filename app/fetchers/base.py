from abc import ABC, abstractmethod
from typing import Optional
from app.models import SearchQuery, TravelOption


class BaseFetcher(ABC):
    """
    Abstract base class for all travel availability fetchers.
    """

    @abstractmethod
    async def fetch(self, query: SearchQuery) -> list[TravelOption]:
        """
        Fetch raw options and normalize them into a list of TravelOption instances.
        """
        pass

    async def search(self, query: SearchQuery) -> list[TravelOption]:
        """
        Fetches options and applies preference filters (class, max price, time window).
        """
        options = await self.fetch(query)
        return self.apply_filters(options, query)

    @staticmethod
    def apply_filters(options: list[TravelOption], query: SearchQuery) -> list[TravelOption]:
        """
        Filters normalized options based on user preferences.
        """
        filtered: list[TravelOption] = []

        for opt in options:
            # 1. Filter by Travel Class (case-insensitive substring/equality)
            if query.classes:
                normalized_classes = [c.upper().strip() for c in query.classes]
                opt_class = opt.travel_class.upper().strip()
                # Check if opt_class matches any of the requested classes
                match = any(c == opt_class or c in opt_class or opt_class in c for c in normalized_classes)
                if not match:
                    continue

            # 2. Filter by Max Price
            if query.max_price is not None and opt.fare is not None:
                if opt.fare > query.max_price:
                    continue

            # 3. Filter by Departure Time (departure_after)
            if query.departure_after:
                if opt.departure_time < query.departure_after:
                    continue

            # 4. Filter by Departure Time (departure_before)
            if query.departure_before:
                if opt.departure_time > query.departure_before:
                    continue

            filtered.append(opt)

        return filtered
