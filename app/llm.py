import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

from app.db import Watch
from app.diff import DiffResult

logger = logging.getLogger(__name__)


class ParsedWatchRequest(BaseModel):
    source: str = Field(description="Origin city or railway station (e.g. Chennai, Bangalore, Delhi)")
    destination: str = Field(description="Destination city or railway station (e.g. Madurai, Kochi, Mumbai)")
    travel_date: str = Field(description="Date of journey in YYYY-MM-DD format")
    mode: Literal["train", "bus", "both"] = Field(
        default="both", description="Mode of travel: 'train', 'bus', or 'both'"
    )
    classes: Optional[list[str]] = Field(
        default=None, description="Preferred travel classes (e.g. ['3A', 'SL'] or ['AC Sleeper'])"
    )
    max_price: Optional[float] = Field(default=None, description="Maximum budget/fare cap in INR")
    departure_after: Optional[str] = Field(
        default=None, description="Earliest departure time in HH:MM format (24hr)"
    )
    departure_before: Optional[str] = Field(
        default=None, description="Latest departure time in HH:MM format (24hr)"
    )
    quota: Optional[str] = Field(
        default="GN", description="Booking quota: 'GN' (General), 'TQ' (Tatkal), 'PT' (Premium Tatkal)"
    )


class LLMService:
    """
    Handles natural language understanding using Google Gemini with structured outputs,
    plus an intelligent rule-based fallback for local offline testing.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self._client = None

        if self.api_key:
            try:
                from google import genai
                self._client = genai.Client(api_key=self.api_key)
                logger.info("Gemini Client initialized successfully.")
            except Exception as e:
                logger.warning("Could not initialize google-genai client: %s", e)

    def parse_watch(self, text: str, reference_date: Optional[datetime] = None) -> Optional[ParsedWatchRequest]:
        """
        Parses a conversational message into a structured ParsedWatchRequest.
        Uses Gemini if configured, otherwise falls back to rule-based regex parsing.
        """
        ref_date = reference_date or datetime.now()

        # 1. Try Gemini Structured Output if API client is available
        if self._client:
            try:
                prompt = f"""
                You are a travel booking assistant parser. Today's date is {ref_date.strftime('%Y-%m-%d (%A)')}.
                Extract travel watch parameters from the user's natural language request.
                Convert any relative date (e.g. 'tomorrow', 'this Friday', 'next Monday', 'Nov 8') into a strict 'YYYY-MM-DD' date based on today's date.
                Map travel mode to 'train', 'bus', or 'both'.

                User request: "{text}"
                """
                response = self._client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": ParsedWatchRequest,
                    },
                )
                if response.text:
                    data = json.loads(response.text)
                    return ParsedWatchRequest(**data)
            except Exception as e:
                logger.error("Gemini parse failed, falling back to rule-based parser: %s", e)

        # 2. Rule-based / Regex Fallback Parser
        return self._fallback_parse(text, ref_date)

    def summarize_alert(self, watch: Watch, diffs: list[DiffResult]) -> str:
        """
        Generates a concise, punchy alert message for Telegram.
        """
        # If Gemini is active, generate a natural human-like summary
        if self._client and len(diffs) > 1:
            try:
                diff_summaries = [f"- {d.message} (Booking: {d.option.booking_url})" for d in diffs[:4]]
                prompt = f"""
                Write a 2-3 line travel ticket alert for a Telegram notification.
                Route: {watch.source} to {watch.destination} on {watch.travel_date}.
                Changes detected:
                {chr(10).join(diff_summaries)}
                Formatting guidelines:
                - Use 🟢 for newly opened seats or RAC seats.
                - Use 🔴 if seats sold out.
                - State Train/Bus Name, Category (Class), and number of available seats.
                - Do not mention low-chance waitlists.
                - Keep it concise, punchy, and action-oriented.
                """
                resp = self._client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                )
                if resp.text:
                    return resp.text.strip()
            except Exception as e:
                logger.warning("Gemini alert summary failed, using template: %s", e)

        # Template-based fallback
        lines = [f"🚨 *TICKET ALERT:* {watch.source} ➔ {watch.destination} ({watch.travel_date})\n"]
        for d in diffs[:5]:
            msg = d.message
            if not any(msg.startswith(prefix) for prefix in ("🟢", "🔴", "🎫", "⚠️", "📉", "🎉", "📈")):
                icon = "🟢" if d.option.is_bookable() else ("🔴" if d.option.status_type in (SeatStatusType.NOT_AVAILABLE, SeatStatusType.REGRET) else "ℹ️")
                msg = f"{icon} {msg}"
            lines.append(msg)

        if len(diffs) > 5:
            lines.append(f"\n_...and {len(diffs) - 5} more updates._")

        return "\n".join(lines)

    @staticmethod
    def _fallback_parse(text: str, ref_date: datetime) -> Optional[ParsedWatchRequest]:
        """
        Heuristic regex parser for common travel watch requests.
        Handles: "Chennai to Madurai, Nov 8, sleeper bus under 900, after 8 PM"
        """
        s = text.strip()

        # Extract "Source to Destination"
        route_match = re.search(r"([A-Za-z\s]+)\s+(?:to|➔|->)\s+([A-Za-z\s]+?)(?:,|\s+on|\s+this|\s+tomorrow|\s+under|\s+after|$)", s, re.IGNORECASE)
        if not route_match:
            return None

        src = route_match.group(1).strip()
        dst = route_match.group(2).strip()

        # Determine travel mode
        lower_text = s.lower()
        if "train" in lower_text and "bus" in lower_text:
            mode = "both"
        elif "train" in lower_text or "irctc" in lower_text:
            mode = "train"
        elif "bus" in lower_text or "sleeper" in lower_text:
            mode = "bus"
        else:
            mode = "both"

        # Determine Date
        travel_date = None
        if "tomorrow" in lower_text:
            travel_date = (ref_date + timedelta(days=1)).strftime("%Y-%m-%d")
        elif "today" in lower_text:
            travel_date = ref_date.strftime("%Y-%m-%d")
        else:
            months_pat = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
            
            # Pattern 1: 'Nov 8', 'November 8th'
            m1 = re.search(rf"\b({months_pat})\s*(\d{{1,2}})(?:st|nd|rd|th)?\b", s, re.IGNORECASE)
            # Pattern 2: '8 Nov', '8th November'
            m2 = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s*({months_pat})\b", s, re.IGNORECASE)

            if m1:
                month_str, day_str = m1.group(1), m1.group(2)
            elif m2:
                day_str, month_str = m2.group(1), m2.group(2)
            else:
                month_str, day_str = None, None

            if month_str and day_str:
                try:
                    parsed_dt = datetime.strptime(f"{day_str} {month_str[:3].title()} {ref_date.year}", "%d %b %Y")
                    if parsed_dt.date() < ref_date.date():
                        parsed_dt = parsed_dt.replace(year=ref_date.year + 1)
                    travel_date = parsed_dt.strftime("%Y-%m-%d")
                except Exception as e:
                    logger.debug("Failed parsing date '%s %s': %s", day_str, month_str, e)

        # Fallback to +5 days if no date found
        if not travel_date:
            travel_date = (ref_date + timedelta(days=5)).strftime("%Y-%m-%d")

        # Classes
        classes = []
        for c in ["SL", "3A", "2A", "1A", "CC", "2S"]:
            if re.search(r"\b" + c + r"\b", s, re.IGNORECASE):
                classes.append(c.upper())
        if "sleeper" in lower_text and "SL" not in classes:
            classes.append("Sleeper")
        if "ac" in lower_text and not any("A" in c for c in classes):
            classes.append("AC")

        # Max price (e.g. "under 900", "below 1200", "< 1500", "₹900")
        price_match = re.search(r"(?:under|below|<|max|budget)\s*(?:₹|rs\.?)?\s*(\d+)", s, re.IGNORECASE)
        max_price = float(price_match.group(1)) if price_match else None

        # Time window (e.g. "after 8 PM", "after 20:00")
        after_match = re.search(r"after\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s, re.IGNORECASE)
        dep_after = None
        if after_match:
            hr = int(after_match.group(1))
            mn = int(after_match.group(2) or 0)
            ampm = (after_match.group(3) or "").lower()
            if ampm == "pm" and hr < 12:
                hr += 12
            elif ampm == "am" and hr == 12:
                hr = 0
            dep_after = f"{hr:02d}:{mn:02d}"

        return ParsedWatchRequest(
            source=src,
            destination=dst,
            travel_date=travel_date,
            mode=mode,
            classes=classes if classes else None,
            max_price=max_price,
            departure_after=dep_after,
            quota="GN",
        )
