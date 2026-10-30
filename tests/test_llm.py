from datetime import datetime
from app.db import Watch
from app.diff import DiffChangeType, DiffResult
from app.llm import LLMService
from app.models import SeatStatusType, TravelMode, TravelOption


def test_fallback_parser():
    llm = LLMService(api_key=None)  # forces fallback regex parser
    ref_date = datetime(2026, 10, 29)

    # 1. Complex natural language test
    text = "Chennai to Madurai, Nov 8, sleeper bus under ₹900, after 8 PM"
    req = llm.parse_watch(text, reference_date=ref_date)
    assert req is not None
    assert req.source.lower() == "chennai"
    assert req.destination.lower() == "madurai"
    assert req.travel_date == "2026-11-08"
    assert req.mode == "bus"
    assert req.max_price == 900.0
    assert req.departure_after == "20:00"
    assert req.classes is not None and "Sleeper" in req.classes

    # 2. Relative date "tomorrow" & train class test
    text2 = "Bangalore to Kochi tomorrow, 3A train"
    req2 = llm.parse_watch(text2, reference_date=ref_date)
    assert req2 is not None
    assert req2.source.lower() == "bangalore"
    assert req2.destination.lower() == "kochi"
    assert req2.travel_date == "2026-10-30"
    assert req2.mode == "train"
    assert req2.classes == ["3A"]


def test_summarize_alert_template():
    llm = LLMService(api_key=None)
    watch = Watch(
        id=1,
        chat_id=123,
        mode="train",
        source="Chennai",
        destination="Madurai",
        travel_date="2026-11-08",
        created_at="2026-10-29",
    )

    diffs = [
        DiffResult(
            watch_id=1,
            option=TravelOption(
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
            ),
            change_type=DiffChangeType.SEAT_OPENED,
            previous_detail="PQWL15",
            current_detail="AVAILABLE 4",
            message="🟢 SEAT OPENED! 12635 - VAIGAI SF EXP (3A) moved from PQWL15 ➔ AVAILABLE 4",
            is_urgent=True,
        )
    ]

    summary = llm.summarize_alert(watch, diffs)
    assert "TICKET ALERT" in summary
    assert "Chennai" in summary
    assert "Madurai" in summary
    assert "SEAT OPENED" in summary
