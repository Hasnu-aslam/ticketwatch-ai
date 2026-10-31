import asyncio
import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.db import Database, Watch
from app.diff import DiffResult
from app.formatter import format_watch_overview
from app.llm import LLMService
from app.scheduler import WatchScheduler

logger = logging.getLogger(__name__)


class TelegramBot:
    """
    Telegram Bot interface for the Festival Ticket Watcher.
    """

    def __init__(
        self,
        token: str,
        db: Database,
        llm_service: LLMService,
        scheduler: Optional[WatchScheduler] = None,
    ):
        self.token = token
        self.db = db
        self.llm = llm_service
        self.scheduler = scheduler
        self.app: Application = ApplicationBuilder().token(self.token).build()
        self._setup_handlers()

    def set_scheduler(self, scheduler: WatchScheduler):
        self.scheduler = scheduler

    def _setup_handlers(self):
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("watches", self.cmd_watches))
        self.app.add_handler(CommandHandler("list", self.cmd_watches))
        self.app.add_handler(CommandHandler("check", self.cmd_check))
        self.app.add_handler(CommandHandler("delete", self.cmd_delete))
        self.app.add_handler(CallbackQueryHandler(self.handle_callback))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = (
            "👋 *Welcome to the Festival Ticket Watcher Agent!*\n\n"
            "I monitor Indian Railways (IRCTC), SETC/TNSTC government buses, and private buses, alerting you the moment a seat opens or a price drops.\n\n"
            "💬 *Simply tell me what you want to watch:*\n"
            "• _\"Chennai to Madurai, Nov 8, sleeper bus under ₹900, after 8 PM\"_\n"
            "• _\"Bangalore to Kochi tomorrow, 3A train\"_\n"
            "• _\"Chennai to Coimbatore, Dec 15, both train and bus\"_\n\n"
            "*Commands:*\n"
            "/watches - List and manage active watches\n"
            "/check <id> - Run instant check (trains + TNSTC + private buses)\n"
            "/delete <id> - Stop watching a route\n"
            "/help - Instructions and tips"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = (
            "ℹ️ *How to Use the Ticket Watcher Agent:*\n\n"
            "1. *Set a Watch:* Send a natural-language message describing your route, date, and preferences.\n"
            "2. *Adaptive Polling Schedule:*\n"
            "   • *Trains:* Scanned every 2.5 hours (bursts to 25s during Tatkal 10 AM & 11 AM on T-1 day, and 5 mins on Chart Prep).\n"
            "   • *Buses:* 4 strategic sweeps a day (08:30, 13:30, 18:30, 22:30) covering TNSTC/SETC & RedBus.\n"
            "3. *Instant Check:* Run `/check <id>` or tap `[ 🔄 Check Now ]` anytime for a live refresh.\n"
            "4. *1-Tap Booking:* Each alert includes direct links to IRCTC or RedBus.\n"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def cmd_watches(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        watches = self.db.get_user_watches(chat_id, only_active=True)

        if not watches:
            await update.message.reply_text("You have no active watches. Send a route to start tracking!")
            return

        await update.message.reply_text(f"📋 *You have {len(watches)} active watch(es):*", parse_mode=ParseMode.MARKDOWN)

        for w in watches:
            flt = w.filters or {}
            details = []
            if flt.get("classes"):
                details.append(f"Classes: {', '.join(flt['classes'])}")
            if flt.get("max_price"):
                details.append(f"Max: ₹{flt['max_price']}")
            if flt.get("departure_after"):
                details.append(f"After: {flt['departure_after']}")

            filter_str = f" ({' | '.join(details)})" if details else ""

            text = (
                f"📍 *Watch #{w.id}:* {w.source} ➔ {w.destination}\n"
                f"📅 *Date:* {w.travel_date} | *Mode:* {w.mode.upper()}{filter_str}\n"
                f"⏱️ Last checked: {w.last_checked_at or 'Pending'}"
            )

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🔄 Check Now", callback_data=f"chk_{w.id}"),
                        InlineKeyboardButton("❌ Stop Watch", callback_data=f"del_{w.id}"),
                    ]
                ]
            )
            await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

    async def cmd_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        args = context.args

        if args and args[0].isdigit():
            watch_id = int(args[0])
            watch = self.db.get_watch(watch_id)
        else:
            # Check user's most recent active watch
            watches = self.db.get_user_watches(chat_id, only_active=True)
            if not watches:
                await update.message.reply_text("No active watches found to check. Send a route first!")
                return
            watch = watches[0]

        if not watch or watch.chat_id != chat_id:
            await update.message.reply_text("Watch not found or does not belong to you.")
            return

        status_msg = await update.message.reply_text(f"🔍 Running instant check for Watch #{watch.id} ({watch.source} ➔ {watch.destination})...")
        if self.scheduler:
            try:
                _, diffs = await self.scheduler.check_watch_now(watch.id)
                options = self.scheduler.get_latest_options(watch.id)
                overview = format_watch_overview(watch, options, diffs=diffs)

                buttons = []
                train_url = f"https://www.confirmtkt.com/rbooking-d/train-search/{watch.source}/{watch.destination}/{watch.travel_date}"
                bus_url = f"https://www.redbus.in/bus-tickets/{watch.source.lower()}-to-{watch.destination.lower()}?date={watch.travel_date}"
                if watch.mode in ("train", "both"):
                    buttons.append(InlineKeyboardButton("🚆 Book Train", url=train_url))
                if watch.mode in ("bus", "both"):
                    buttons.append(InlineKeyboardButton("🚌 Book Bus", url=bus_url))

                control_row = [
                    InlineKeyboardButton("🔄 Refresh", callback_data=f"chk_{watch.id}"),
                    InlineKeyboardButton("❌ Stop Watch", callback_data=f"del_{watch.id}"),
                ]
                keyboard = InlineKeyboardMarkup([buttons, control_row] if buttons else [control_row])

                await status_msg.edit_text(overview, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.error("Error during manual check: %s", e)
                await status_msg.edit_text(f"⚠️ Error running check: {e}")

    async def cmd_delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        args = context.args
        if not args or not args[0].isdigit():
            await update.message.reply_text("Usage: `/delete <watch_id>` (e.g. `/delete 1`)", parse_mode=ParseMode.MARKDOWN)
            return

        watch_id = int(args[0])
        success = self.db.deactivate_watch(watch_id, chat_id=chat_id)
        if success:
            await update.message.reply_text(f"✅ Watch #{watch_id} stopped.")
        else:
            await update.message.reply_text(f"❌ Watch #{watch_id} not found or already inactive.")

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        data = query.data
        if data.startswith("del_"):
            watch_id = int(data.split("_")[1])
            self.db.deactivate_watch(watch_id, chat_id=query.message.chat_id)
            await query.edit_message_text(f"🛑 Watch #{watch_id} has been stopped.")
        elif data.startswith("chk_"):
            watch_id = int(data.split("_")[1])
            await query.edit_message_text(f"🔍 Checking Watch #{watch_id} live...")
            if self.scheduler:
                try:
                    watch, diffs = await self.scheduler.check_watch_now(watch_id)
                    if not watch:
                        await query.edit_message_text("⚠️ Watch not found.")
                        return

                    options = self.scheduler.get_latest_options(watch_id)
                    overview = format_watch_overview(watch, options, diffs=diffs)

                    buttons = []
                    train_url = f"https://www.confirmtkt.com/rbooking-d/train-search/{watch.source}/{watch.destination}/{watch.travel_date}"
                    bus_url = f"https://www.redbus.in/bus-tickets/{watch.source.lower()}-to-{watch.destination.lower()}?date={watch.travel_date}"
                    if watch.mode in ("train", "both"):
                        buttons.append(InlineKeyboardButton("🚆 Book Train", url=train_url))
                    if watch.mode in ("bus", "both"):
                        buttons.append(InlineKeyboardButton("🚌 Book Bus", url=bus_url))

                    control_row = [
                        InlineKeyboardButton("🔄 Refresh", callback_data=f"chk_{watch.id}"),
                        InlineKeyboardButton("❌ Stop Watch", callback_data=f"del_{watch.id}"),
                    ]
                    keyboard = InlineKeyboardMarkup([buttons, control_row] if buttons else [control_row])

                    await query.edit_message_text(overview, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
                except Exception as e:
                    await query.edit_message_text(f"⚠️ Check failed: {e}")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        text = update.message.text

        # 1. Parse natural language request
        parsed = self.llm.parse_watch(text)
        if not parsed:
            await update.message.reply_text(
                "🤔 I couldn't extract the route and date from your message.\n\n"
                "Please try something like:\n"
                "_\"Chennai to Madurai, Nov 8, sleeper bus under ₹900, after 8 PM\"_",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # 2. Build filters dictionary
        filters = {}
        if parsed.classes:
            filters["classes"] = parsed.classes
        if parsed.max_price:
            filters["max_price"] = parsed.max_price
        if parsed.departure_after:
            filters["departure_after"] = parsed.departure_after
        if parsed.departure_before:
            filters["departure_before"] = parsed.departure_before
        if parsed.quota:
            filters["quota"] = parsed.quota

        # 3. Save watch to database
        watch = self.db.create_watch(
            chat_id=chat_id,
            source=parsed.source,
            destination=parsed.destination,
            travel_date=parsed.travel_date,
            mode=parsed.mode,
            filters=filters,
        )

        # 4. Confirmation message
        summary_lines = [
            f"✅ *Watch #{watch.id} Created!*",
            f"📍 *Route:* {watch.source} ➔ {watch.destination}",
            f"📅 *Date:* {watch.travel_date}",
            f"🚆 *Mode:* {watch.mode.upper()}",
        ]
        if parsed.classes:
            summary_lines.append(f"🎯 *Classes:* {', '.join(parsed.classes)}")
        if parsed.max_price:
            summary_lines.append(f"💰 *Max Price:* ₹{parsed.max_price}")
        if parsed.departure_after:
            summary_lines.append(f"⏰ *After:* {parsed.departure_after}")

        summary_lines.append("\n🔍 _Checking live availability right now..._")

        await update.message.reply_text("\n".join(summary_lines), parse_mode=ParseMode.MARKDOWN)

        # 5. Immediate first check
        if self.scheduler:
            try:
                diffs = await self.scheduler.check_watch(watch, force_bus=True)
                options = self.scheduler.get_latest_options(watch.id)
                overview = format_watch_overview(watch, options, diffs=diffs)

                buttons = []
                train_url = f"https://www.confirmtkt.com/rbooking-d/train-search/{watch.source}/{watch.destination}/{watch.travel_date}"
                bus_url = f"https://www.redbus.in/bus-tickets/{watch.source.lower()}-to-{watch.destination.lower()}?date={watch.travel_date}"
                if watch.mode in ("train", "both"):
                    buttons.append(InlineKeyboardButton("🚆 Book Train", url=train_url))
                if watch.mode in ("bus", "both"):
                    buttons.append(InlineKeyboardButton("🚌 Book Bus", url=bus_url))

                control_row = [
                    InlineKeyboardButton("🔄 Refresh", callback_data=f"chk_{watch.id}"),
                    InlineKeyboardButton("❌ Stop Watch", callback_data=f"del_{watch.id}"),
                ]
                keyboard = InlineKeyboardMarkup([buttons, control_row] if buttons else [control_row])

                await update.message.reply_text(
                    overview,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as e:
                logger.error("Error running initial check for watch #%d: %s", watch.id, e)
                await update.message.reply_text(f"⚠️ Error running initial availability check: {e}")

    async def send_alert(self, watch: Watch, diffs: list[DiffResult]):
        """
        Sends an alert notification to the user's Telegram chat with inline actions.
        """
        if not diffs:
            return

        text = self.llm.summarize_alert(watch, diffs)

        # Build inline buttons (Booking deep links + Refresh + Stop watch button)
        buttons = []
        booking_url = next((d.option.booking_url for d in diffs if d.option.booking_url), None)
        if booking_url:
            buttons.append(InlineKeyboardButton("🎟️ Book Now", url=booking_url))

        buttons.append(InlineKeyboardButton("🔄 Check Now", callback_data=f"chk_{watch.id}"))
        buttons.append(InlineKeyboardButton("❌ Stop Watch", callback_data=f"del_{watch.id}"))
        keyboard = InlineKeyboardMarkup([buttons])

        try:
            await self.app.bot.send_message(
                chat_id=watch.chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            # Fallback without Markdown in case of special characters
            logger.warning("Markdown send failed, trying plain text: %s", e)
            await self.app.bot.send_message(
                chat_id=watch.chat_id,
                text=text.replace("*", "").replace("_", ""),
                reply_markup=keyboard,
            )
