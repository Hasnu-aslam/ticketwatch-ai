"""
Interactive CLI to test Phase 1 Fetchers directly.
Usage:
  python demo_fetch.py "Chennai" "Madurai" "2026-11-08"
  python demo_fetch.py "Bangalore" "Kochi" "2026-11-12" --classes "3A,SL" --max-price 1500
"""

import argparse
import asyncio
from datetime import datetime, timedelta

from app.fetchers.private_bus import PrivateBusFetcher
from app.fetchers.tnstc import TNSTCFetcher
from app.fetchers.train import ConfirmTktTrainFetcher
from app.models import SearchQuery, TravelMode


async def main():
    parser = argparse.ArgumentParser(description="Fetch and normalize train & bus availability.")
    parser.add_argument("source", nargs="?", default="Chennai", help="Source city or station code")
    parser.add_argument("destination", nargs="?", default="Madurai", help="Destination city or station code")
    parser.add_argument(
        "date",
        nargs="?",
        default=(datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d"),
        help="Travel date (YYYY-MM-DD)",
    )
    parser.add_argument("--mode", choices=["train", "bus", "both"], default="both", help="Travel mode to search")
    parser.add_argument("--classes", default="", help="Comma-separated class filters (e.g. '3A,SL' or 'AC Sleeper')")
    parser.add_argument("--max-price", type=float, default=None, help="Maximum price cap (e.g. 1000)")
    parser.add_argument("--after", default=None, help="Departure after time (HH:MM)")
    parser.add_argument("--before", default=None, help="Departure before time (HH:MM)")

    args = parser.parse_args()

    classes_filter = [c.strip() for c in args.classes.split(",") if c.strip()] or None

    query = SearchQuery(
        source=args.source,
        destination=args.destination,
        travel_date=args.date,
        classes=classes_filter,
        max_price=args.max_price,
        departure_after=args.after,
        departure_before=args.before,
    )

    print(f"\n🔍 Searching options: {args.source} ➔ {args.destination} on {args.date}")
    if classes_filter:
        print(f"🎯 Class filter: {classes_filter}")
    if args.max_price:
        print(f"💰 Max price: ₹{args.max_price}")
    if args.after or args.before:
        print(f"⏰ Time window: after {args.after} / before {args.before}")
    print("-" * 75)

    options = []

    # 1. Train fetcher
    if args.mode in ("train", "both"):
        print("🚆 Fetching live trains (IRCTC / ConfirmTkt)...")
        try:
            train_fetcher = ConfirmTktTrainFetcher()
            train_results = await train_fetcher.search(query)
            options.extend(train_results)
            print(f"   ✓ Found {len(train_results)} matching train options.")
        except Exception as e:
            print(f"   ✗ Train fetch failed: {e}")

    # 2. TNSTC government bus fetcher
    if args.mode in ("bus", "both"):
        print("🏛️ Fetching government buses (SETC / TNSTC)...")
        try:
            tnstc_fetcher = TNSTCFetcher()
            tnstc_results = await tnstc_fetcher.search(query)
            options.extend(tnstc_results)
            print(f"   ✓ Found {len(tnstc_results)} matching TNSTC options.")
        except Exception as e:
            print(f"   ✗ TNSTC fetch failed: {e}")

        print("🚌 Fetching private buses (RedBus / Playwright)...")
        try:
            pvt_fetcher = PrivateBusFetcher()
            pvt_results = await pvt_fetcher.search(query)
            options.extend(pvt_results)
            print(f"   ✓ Found {len(pvt_results)} matching private bus options.")
        except Exception as e:
            print(f"   ✗ Private bus fetch failed: {e}")


    print("\n" + "=" * 75)
    print(f"TOTAL MATCHING OPTIONS: {len(options)}")
    print("=" * 75)

    # Sort options by departure time
    options.sort(key=lambda x: x.departure_time)

    for i, opt in enumerate(options[:20], 1):
        mode_icon = "🚆" if opt.mode == TravelMode.TRAIN else "🚌"
        status_color = "🟢" if opt.is_bookable() else ("🟡" if "WL" in opt.status_type.value else "🔴")
        fare_str = f"₹{int(opt.fare)}" if opt.fare else "N/A"

        print(f"\n{i:2d}. {mode_icon} [{opt.mode.value.upper()}] {opt.operator_name}")
        print(f"    Class: {opt.travel_class} | Time: {opt.departure_time} ➔ {opt.arrival_time} ({opt.duration or 'N/A'})")
        print(f"    Status: {status_color} {opt.status_type.value} ({opt.status_detail}) | Fare: {fare_str}")
        if opt.booking_url:
            print(f"    Link: {opt.booking_url}")

    if len(options) > 20:
        print(f"\n... and {len(options) - 20} more options.")


if __name__ == "__main__":
    asyncio.run(main())
