# Festival Ticket Watcher Agent

An AI agent that keeps checking bus and train ticket availability during festival rush, filters results by your preferences, and sends alerts when something opens up or a price drops.

## Problem

During festival season, good bus deals vanish fast and train tickets are hard to get. Manually refreshing booking sites is tedious and easy to miss. This agent does the watching and tells you when to act.

## Scope

- **Monitor and alert only.** Booking is done manually by the user.
- IRCTC has no public availability API, uses captcha/OTP, and blocks bots. Automating booking violates its terms.
- Poll at a polite rate (5-15 minutes) to avoid being blocked.

## Features

- Natural-language watch setup: "Chennai to Madurai, Nov 8, sleeper bus under ₹900, after 8 PM"
- Preference filters: source, destination, date(s), train class (SL/3A/2A), bus type (sleeper/AC/seater), max price, time window
- Change detection: notifies only on changes (seat opened, price dropped, WL → RAC → CNF)
- Telegram alerts with short LLM-written summaries
- Tatkal-aware polling: faster checks around Tatkal opening times

## Architecture

```
User (Telegram)
   │  natural-language request
   ▼
LLM parser ──► structured watch (JSON) ──► SQLite
                                              │
Scheduler (APScheduler) ──► Fetchers ──► Filter + Diff ──► Notifier (Telegram)
                            ├─ train
                            └─ bus                 │
                                                   ▼
                                         LLM summary of changes
```

The LLM is kept **out of the polling loop**. It is called only to parse requests and summarize alerts, which keeps cost low.

## Components

| Component | Responsibility |
|---|---|
| Preferences | Route, dates, class/bus type, price cap, time window |
| Scheduler | Runs each watch on an interval |
| Fetchers | One module per source, returning normalized results |
| Filter + Diff | Applies preferences, compares to last snapshot |
| Notifier | Sends Telegram messages |
| Store | SQLite: watches and snapshots |
| LLM layer | Parses watches, summarizes alerts, tool calling |

## Data Sources

| Source | Notes |
|---|---|
| IRCTC | No public API. Monitor only |
| Aggregators (ConfirmTkt, RailYatri) | Unofficial endpoints, change often |
| Paid third-party APIs (e.g. RapidAPI wrappers) | Most stable for a prototype |
| Bus portals (redBus, AbhiBus, state transport) | Partner APIs are restricted. Browser automation is an option, check ToS |

## Tech Stack

- Python
- `httpx` or Playwright for fetching
- APScheduler for scheduling
- SQLite for storage
- `python-telegram-bot` for notifications
- An LLM with function calling

## Agent Tools

Exposed to the LLM via function calling:

- `search_trains(source, destination, date, class)`
- `search_buses(source, destination, date, bus_type, max_price)`
- `create_watch(preferences)`
- `list_watches()`
- `delete_watch(id)`

## Data Model (sketch)

```sql
CREATE TABLE watches (
  id INTEGER PRIMARY KEY,
  mode TEXT,              -- train | bus
  source TEXT,
  destination TEXT,
  travel_date TEXT,
  filters JSON,           -- class, bus type, max price, time window
  interval_min INTEGER DEFAULT 10,
  active INTEGER DEFAULT 1
);

CREATE TABLE snapshots (
  id INTEGER PRIMARY KEY,
  watch_id INTEGER,
  taken_at TEXT,
  results JSON
);
```

## Quickstart

### 1. Setup Environment
```bash
# Setup virtual environment
uv venv .venv
source .venv/bin/activate

# Install dependencies
uv pip install -r requirements.txt
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Update `.env` with:
- `TELEGRAM_BOT_TOKEN`: From [@BotFather](https://t.me/BotFather) on Telegram
- `GEMINI_API_KEY`: Google Gemini API key (optional, smart fallback parser enabled by default)

### 3. Test Live Availability via CLI
Test live Indian Railways & Bus route fetching directly from terminal:
```bash
.venv/bin/python demo_fetch.py "Chennai" "Madurai" --classes "3A,SL,AC Sleeper"
```

### 4. Run Automated Test Suite
```bash
.venv/bin/pytest -v
```

### 5. Launch the Agent & Telegram Bot
```bash
.venv/bin/python -m app.main
```

## Build Plan Status
- [x] **Phase 1: Fetchers & Normalizers** (ConfirmTkt live trains + RedBus deep links)
- [x] **Phase 2: DB & Diff Engine** (SQLite snapshots + zero-spam diff detection)
- [x] **Phase 3: Telegram Bot** (Interactive cards with 1-tap booking & cancel actions)
- [x] **Phase 4: LLM Natural Language Layer** (Gemini structured outputs + fallback parser)
- [x] **Phase 5: Tatkal Window Detection** (Burst polling at 10 AM & 11 AM)
- [x] **Phase 6: TNSTC / SETC Government Buses** (Live endpoint query + government fare templates)
- [x] **Phase 7: Playwright Private Bus Scraper** ($0 API cost, network JSON interception & resource blocking)
- [x] **Phase 8: Adaptive Tiered Polling** (4 daily bus sweeps + 2.5h train regular + on-demand `/check`)

## Adaptive Polling Schedule

| Transport | Polling Frequency | Target Windows / Details |
|---|---|---|
| **Trains (Tatkal Burst)** | **Every 25 seconds** (for 6 mins) | Active **only** on Journey Date - 1 day at `09:59–10:06` (AC) and `10:59–11:06` (SL) |
| **Trains (Chart Prep)** | **Every 5 minutes** | Active on Journey Date (T-0) 4 hours before departure |
| **Trains (Regular)** | **Every 2.5 hours** | Normal daytime waitlist checks (07:00 to 23:00) |
| **Buses (TNSTC & Private)** | **4 times a day** | Scheduled sweeps at `08:30`, `13:30`, `18:30`, and `22:30` |
| **Night Quiet Hours** | **Idle (No polling)** | Between `23:45` and `06:00` (matches IRCTC server maintenance) |
| **On-Demand** | **Instant** | Send `/check <id>` or tap `[ 🔄 Check Now ]` in Telegram |

## Project Structure

```
booking_agent/
├── README.md
├── requirements.txt
├── .env.example
├── demo_fetch.py
├── app/
│   ├── main.py
│   ├── scheduler.py
│   ├── bot.py
│   ├── llm.py
│   ├── db.py
│   ├── diff.py
│   ├── station_resolver.py
│   └── fetchers/
│       ├── base.py
│       ├── train.py
│       ├── bus.py
│       ├── tnstc.py
│       └── private_bus.py
└── tests/
    ├── test_fetchers.py
    ├── test_db_and_diff.py
    ├── test_llm.py
    └── test_scheduler.py
```

## Risks and Notes

- Source sites and unofficial APIs can change without notice, so keep fetchers isolated and easy to replace.
- Respect each site's terms of service and rate limits.
- Never store IRCTC credentials in the agent.
- Keep API keys in `.env`, not in the repo.

## Future Ideas

- Price history and "best time to buy" insights
- Multi-leg and alternate-route suggestions
- Group watches for family travel
- Web dashboard for managing watches