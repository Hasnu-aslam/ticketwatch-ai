import asyncio
import logging
import os
import signal
import sys
from dotenv import load_dotenv

import json
import httpx

from datetime import datetime, timezone

from app.bot import TelegramBot
from app.db import Database
from app.llm import LLMService
from app.scheduler import WatchScheduler

# Load environment variables
load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO"),
)
logger = logging.getLogger("festival_ticket_watcher")


async def handle_http_request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, db: Database):
    """
    Lightweight HTTP server responding to Render health check pings.
    Keeps the Render Web Service active and satisfies Render's PORT requirement.
    """
    try:
        # Read HTTP request line and headers
        request_line = await reader.readline()
        while True:
            header_line = await reader.readline()
            if header_line in (b"\r\n", b"\n", b""):
                break

        try:
            active_count = len(db.get_active_watches())
        except Exception:
            active_count = 0

        payload = json.dumps({
            "status": "healthy",
            "service": "festival_ticket_watcher",
            "active_watches": active_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        response = (
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
            f"Connection: close\r\n\r\n"
            f"{payload}"
        )
        writer.write(response.encode("utf-8"))
        await writer.drain()
    except Exception as e:
        logger.debug("HTTP health check error: %s", e)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def start_self_pinger(ping_url: str, interval_sec: int = 600):
    """
    Pings the Render public URL every 10 minutes to prevent the container from sleeping.
    """
    logger.info("Self-pinger enabled for: %s (interval: %ds)", ping_url, interval_sec)
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            await asyncio.sleep(interval_sec)
            try:
                resp = await client.get(ping_url)
                logger.info("Self-ping response: %d", resp.status_code)
            except Exception as e:
                logger.warning("Self-ping failed: %s", e)


async def main():
    logger.info("Starting Festival Ticket Watcher Agent...")

    db_path = os.getenv("DATABASE_PATH", "booking_watcher.db")
    db = Database(db_path=db_path)

    # 1. Start HTTP health check server for Render (binds to $PORT)
    port = int(os.getenv("PORT", "8080"))
    http_server = await asyncio.start_server(
        lambda r, w: handle_http_request(r, w, db),
        "0.0.0.0",
        port,
    )
    logger.info("🌐 Health check server listening on 0.0.0.0:%d (Ready for Render pings)", port)

    # 2. Check for Render external URL to self-ping
    ping_url = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("PING_URL")
    pinger_task = None
    if ping_url:
        target_ping = ping_url.rstrip("/") + "/health"
        pinger_task = asyncio.create_task(start_self_pinger(target_ping, interval_sec=600))

    gemini_key = os.getenv("GEMINI_API_KEY")
    llm_service = LLMService(api_key=gemini_key)

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        logger.warning(
            "⚠️ TELEGRAM_BOT_TOKEN is not set in .env! "
            "Please create a bot via @BotFather on Telegram and add its token to .env."
        )
        print("\n" + "!" * 70)
        print("CRITICAL: TELEGRAM_BOT_TOKEN is missing in your .env file!")
        print("1. Open Telegram and search for @BotFather")
        print("2. Send /newbot to create your bot and copy the token")
        print("3. Add TELEGRAM_BOT_TOKEN=your_token in .env")
        print("!" * 70 + "\n")
        sys.exit(1)

    # Initialize bot
    bot = TelegramBot(token=bot_token, db=db, llm_service=llm_service)

    # Initialize scheduler with bot notifier callback
    scheduler = WatchScheduler(db=db, notifier=bot.send_alert)
    bot.set_scheduler(scheduler)

    poll_interval = int(os.getenv("POLL_INTERVAL_SEC", "9000"))

    # Graceful shutdown handler
    stop_event = asyncio.Event()

    def handle_signal():
        logger.info("Shutdown signal received...")
        scheduler.stop()
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            pass  # Windows or specific runtimes

    logger.info("Initializing Telegram Bot and Background Watcher...")
    await bot.app.initialize()
    await bot.app.start()
    await bot.app.updater.start_polling()

    # Start the scheduler loop as a background task
    scheduler_task = asyncio.create_task(scheduler.start(default_interval_sec=poll_interval))

    logger.info("🚀 Agent is online and watching! Press Ctrl+C to stop.")

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down bot and scheduler...")
        if pinger_task:
            pinger_task.cancel()
        scheduler_task.cancel()
        await bot.app.updater.stop()
        await bot.app.stop()
        await bot.app.shutdown()
        http_server.close()
        await http_server.wait_closed()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
