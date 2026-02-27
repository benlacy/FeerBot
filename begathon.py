#!/usr/bin/env python3
"""
Begathon - Countdown timer that adds time when subscriptions are received.
Uses EventSub webhooks for channel.subscription.message (share on stream) and
channel.subscription.gift. Sends timer state to begathon_overlay.html via WebSocket.

Run server.py (ws://localhost:6790) for overlay updates.
"""

import argparse
import asyncio
import json
import logging
import os
import hmac
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from aiohttp import web
import aiohttp
import websockets

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)
logging.getLogger("websockets").setLevel(logging.WARNING)

load_dotenv()

# --- Timer state ---
TIMER_DATA_FILE = "data/begathon_timer_data.json"
OVERLAY_WS_URL = "ws://localhost:6790"
CHART_UPDATE_INTERVAL = 10  # seconds between chart history updates
CHART_HISTORY_DURATION = 3600  # 1 hour in seconds

# Time added per subscription (in seconds) - easily modifiable
TIME_TIER1 = 6 * 60   # 3 minutes for Tier 1 / Prime (Twitch tier 1000)
TIME_TIER2 = TIME_TIER1 * 2   # 6 minutes for Tier 2 (Twitch tier 2000)
TIME_TIER3 = TIME_TIER1 * 6  # 15 minutes for Tier 3 (Twitch tier 3000)
TIME_GIFTED = 0 * 60  # 3 minutes per gifted sub (all tiers)

_timer_seconds = 0
_timer_history = []  # [{timestamp, timer_seconds}, ...]
_overlay_ws = None
_app_token = None
_client_id = None
_client_secret = None
_broadcaster_id = None
_last_chart_update = None


def _get_time_for_tier(tier: str, is_gift: bool) -> int:
    """Return seconds to add based on sub tier. Tier: 1000, 2000, 3000."""
    if is_gift:
        return TIME_GIFTED
    tier_map = {"1000": TIME_TIER1, "2000": TIME_TIER2, "3000": TIME_TIER3}
    return tier_map.get(tier, TIME_TIER1)


def _load_timer_data() -> tuple[int, list]:
    """Load timer state from JSON file."""
    global _timer_seconds, _timer_history
    data_file = Path(TIMER_DATA_FILE)
    if data_file.exists():
        try:
            with open(data_file, "r") as f:
                data = json.load(f)
                _timer_seconds = data.get("timer_seconds", 0)
                _timer_history = data.get("timer_history", [])
        except Exception as e:
            logger.error("Error loading timer data: %s", e)
            _timer_seconds = 0
            _timer_history = []
    return _timer_seconds, _timer_history


def _save_timer_data() -> None:
    """Save timer state to JSON file."""
    data_file = Path(TIMER_DATA_FILE)
    data_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(data_file, "w") as f:
            json.dump({
                "timer_seconds": _timer_seconds,
                "timer_history": _timer_history,
            }, f, indent=2)
    except Exception as e:
        logger.error("Error saving timer data: %s", e)


def _append_timer_history() -> None:
    """Append current timer to history, keep last CHART_HISTORY_DURATION."""
    global _timer_history
    now = datetime.now(timezone.utc)
    _timer_history.append({
        "timestamp": now.isoformat(),
        "timer_seconds": _timer_seconds,
    })
    cutoff_ts = now.timestamp() - CHART_HISTORY_DURATION
    _timer_history = [
        e for e in _timer_history
        if datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp() > cutoff_ts
    ]
    # Limit to ~1 hour at 10s intervals = 360 entries
    if len(_timer_history) > 400:
        _timer_history = _timer_history[-400:]


def _generate_test_history(duration_seconds: int = 3600, interval: int = 10) -> list[dict]:
    """
    Generate fake timer history for --test mode.
    Simulates ~1 hour: countdown with occasional sub bumps (time added).
    """
    now = datetime.now(timezone.utc)
    history = []
    # Sub bumps: (elapsed_sec, seconds_added) - spread over the hour
    bumps = [
        (600, 180),   # +3 min at 10 min
        (1200, 360),  # +6 min at 20 min
        (2100, 180),  # +3 min at 35 min
        (2700, 540),  # +9 min at 45 min
        (3300, 180),  # +3 min at 55 min
    ]
    for elapsed in range(0, duration_seconds + 1, interval):
        total_bumps = sum(sec for (e, sec) in bumps if e <= elapsed)
        t = max(0, duration_seconds - elapsed + total_bumps)
        ts = datetime.fromtimestamp(now.timestamp() - (duration_seconds - elapsed), tz=timezone.utc)
        history.append({"timestamp": ts.isoformat(), "timer_seconds": t})
    return history


def _parse_twitch_timestamp(ts: str) -> datetime | None:
    """Parse Twitch RFC3339 timestamp."""
    if not ts:
        return None
    s = ts.replace("Z", "+00:00")
    if len(s) > 26 and s[19] == ".":
        frac = s[20:-6]
        if len(frac) > 6:
            s = s[:26] + s[-6:]
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


async def _send_timer_to_overlay() -> bool:
    """Send timer state to OBS overlay via WebSocket."""
    global _overlay_ws
    if _overlay_ws is None or _overlay_ws.state != websockets.State.OPEN:
        return False
    try:
        payload = {
            "type": "begathon_timer",
            "timer_seconds": _timer_seconds,
            "timer_history": _timer_history,
        }
        await _overlay_ws.send(json.dumps(payload))
        return True
    except websockets.exceptions.ConnectionClosed:
        _overlay_ws = None
        return False
    except Exception:
        return False


# --- Configuration ---
def load_config():
    """Load and validate config."""
    client_id = os.getenv("TWITCH_APP_CLIENT_ID")
    client_secret = os.getenv("TWITCH_APP_CLIENT_SECRET")
    broadcaster_id = os.getenv("BROADCASTER_ID")
    webhook_secret = os.getenv("WEBHOOK_SECRET")
    webhook_port = int(os.getenv("WEBHOOK_PORT", "8081"))
    webhook_base_url = os.getenv("WEBHOOK_BASE_URL", "").rstrip("/")

    if not all([client_id, client_secret, broadcaster_id, webhook_secret]):
        raise ValueError("Missing required env: TWITCH_APP_CLIENT_ID, TWITCH_APP_CLIENT_SECRET, BROADCASTER_ID, WEBHOOK_SECRET")
    if webhook_base_url and not webhook_base_url.startswith("https://"):
        logger.warning("WEBHOOK_BASE_URL must be HTTPS (e.g. ngrok).")

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "broadcaster_id": broadcaster_id,
        "webhook_secret": webhook_secret,
        "webhook_port": webhook_port,
        "webhook_base_url": webhook_base_url,
    }


def verify_signature(message_id: str, timestamp: str, body: str, signature: str, secret: str) -> bool:
    """Verify Twitch EventSub message signature."""
    if not signature or not secret or not message_id or not timestamp:
        return False
    hmac_message = message_id + timestamp + body
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        hmac_message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def get_app_access_token(client_id: str, client_secret: str) -> str:
    """Get an app access token using client_credentials."""
    url = "https://id.twitch.tv/oauth2/token"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=payload) as resp:
            text = await resp.text()
            if resp.status != 200:
                logger.error("App token failed: %s", text[:200])
                raise RuntimeError(f"OAuth failed: {text}")
            data = json.loads(text)
            return data["access_token"]


async def delete_existing_eventsub(
    app_token: str, client_id: str, broadcaster_id: str, sub_type: str
) -> None:
    """Delete existing EventSub subscription of given type."""
    url = "https://api.twitch.tv/helix/eventsub/subscriptions"
    headers = {"Authorization": f"Bearer {app_token}", "Client-Id": client_id}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            text = await resp.text()
            if resp.status != 200:
                logger.warning("Could not list subscriptions: %s %s", resp.status, text[:200])
                return
            data = json.loads(text)
            subs = data.get("data", [])
            to_delete = [
                s for s in subs
                if s.get("type") == sub_type
                and s.get("condition", {}).get("broadcaster_user_id") == broadcaster_id
            ]
            for sub in to_delete:
                sub_id = sub.get("id")
                async with session.delete(url, params={"id": sub_id}, headers=headers) as del_resp:
                    if del_resp.status in (204, 200):
                        logger.info("Removed existing %s", sub_type)
                    else:
                        logger.warning("Delete failed for %s: %s", sub_id, await del_resp.text())


async def create_eventsub_subscription(
    app_token: str,
    client_id: str,
    broadcaster_id: str,
    callback_url: str,
    secret: str,
    sub_type: str,
    version: str = "1",
) -> None:
    """Create EventSub subscription."""
    url = "https://api.twitch.tv/helix/eventsub/subscriptions"
    body = {
        "type": sub_type,
        "version": version,
        "condition": {"broadcaster_user_id": broadcaster_id},
        "transport": {"method": "webhook", "callback": callback_url, "secret": secret},
    }
    headers = {
        "Authorization": f"Bearer {app_token}",
        "Client-Id": client_id,
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=body, headers=headers) as resp:
            response_text = await resp.text()
            if resp.status == 202:
                logger.info("Subscribed: %s (pending verification)", sub_type)
                return
            if resp.status == 409:
                logger.info("Subscribed: %s (already exists)", sub_type)
                return
            logger.error("EventSub subscription failed: %s", response_text)
            raise RuntimeError(f"EventSub create failed: {response_text}")


async def handle_eventsub(request: web.Request, webhook_secret: str):
    """Handle Twitch EventSub: verification challenge and notifications."""
    global _timer_seconds, _timer_history
    body = await request.text()
    message_id = request.headers.get("Twitch-Eventsub-Message-Id", "")
    timestamp = request.headers.get("Twitch-Eventsub-Message-Timestamp", "")
    signature = request.headers.get("Twitch-Eventsub-Message-Signature", "")
    if webhook_secret and not verify_signature(message_id, timestamp, body, signature, webhook_secret):
        logger.warning("Webhook: signature FAILED")
        return web.Response(status=403, text="Forbidden")

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        logger.error("Webhook: invalid JSON %s", e)
        return web.Response(status=400, text="Bad Request")

    message_type = request.headers.get("Twitch-Eventsub-Message-Type", "")

    if message_type == "webhook_callback_verification":
        challenge = data.get("challenge", "")
        return web.Response(status=200, body=challenge.encode("utf-8"), content_type="text/plain")

    if message_type == "notification":
        subscription = data.get("subscription", {})
        event = data.get("event", {})
        sub_type = subscription.get("type", "")
        if sub_type == "channel.subscription.message":
            user_name = event.get("user_name") or event.get("user_login", "?")
            tier = event.get("tier", "1000")
            seconds = _get_time_for_tier(tier, is_gift=False)
            _timer_seconds += seconds
            _append_timer_history()
            _save_timer_data()
            await _send_timer_to_overlay()
            logger.info("Webhook: share -> %s (tier %s) +%ds, timer=%ds", user_name, tier, seconds, _timer_seconds)
        elif sub_type == "channel.subscription.gift":
            user_name = event.get("user_name") or event.get("user_login", "?")
            total = event.get("total", 1)
            tier = event.get("tier", "1000")
            seconds_per = _get_time_for_tier(tier, is_gift=True)
            seconds = total * seconds_per
            _timer_seconds += seconds
            _append_timer_history()
            _save_timer_data()
            await _send_timer_to_overlay()
            logger.info("Webhook: gift -> %s x%d (tier %s) +%ds, timer=%ds", user_name, total, tier, seconds, _timer_seconds)
        return web.Response(status=200)

    if message_type == "revocation":
        logger.info("Webhook: subscription revoked")
        return web.Response(status=200)

    return web.Response(status=200)


async def _overlay_websocket_loop() -> None:
    """Maintain WebSocket connection to overlay server and send timer updates."""
    global _overlay_ws
    while True:
        try:
            if _overlay_ws is None or _overlay_ws.state != websockets.State.OPEN:
                async with websockets.connect(OVERLAY_WS_URL) as ws:
                    _overlay_ws = ws
                    await _send_timer_to_overlay()
                    await ws.wait_closed()
                    _overlay_ws = None
            else:
                await asyncio.sleep(1)
        except websockets.exceptions.ConnectionClosed:
            _overlay_ws = None
        except Exception:
            _overlay_ws = None
        await asyncio.sleep(2)


async def _countdown_loop() -> None:
    """Count down timer every second and update overlay. Append to history every 10s."""
    global _timer_seconds, _timer_history, _last_chart_update
    _last_chart_update = datetime.now(timezone.utc)
    while True:
        try:
            if _timer_seconds > 0:
                _timer_seconds -= 1
            now = datetime.now(timezone.utc)
            if (now - _last_chart_update).total_seconds() >= CHART_UPDATE_INTERVAL:
                _append_timer_history()
                _save_timer_data()
                _last_chart_update = now
            await _send_timer_to_overlay()
        except Exception as e:
            logger.exception("Countdown error: %s", e)
        await asyncio.sleep(1)


@web.middleware
async def log_all_requests(request: web.Request, handler):
    """Log request and pass to handler."""
    try:
        if request.method == "POST" and request.path == "/eventsub":
            msg_type = request.headers.get("Twitch-Eventsub-Message-Type", "?")
            logger.info("  -> POST /eventsub %s", msg_type)
        return await handler(request)
    except Exception as e:
        logger.exception("Handler error: %s", e)
        raise


async def run_webhook_server(webhook_port: int, webhook_secret: str):
    """Start the HTTP server that receives Twitch webhooks."""
    app = web.Application(middlewares=[log_all_requests])
    app.router.add_post("/eventsub", lambda r: handle_eventsub(r, webhook_secret))

    async def health(request):
        return web.Response(
            text=f"Begathon webhook OK (port {webhook_port}). Timer: {_timer_seconds}s\n"
        )

    app.router.add_get("/eventsub", health)
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", webhook_port)
    await site.start()
    logger.info("Webhook server: 0.0.0.0:%s/eventsub", webhook_port)
    return runner


async def _check_webhook_reachable(callback_url: str, webhook_port: int) -> None:
    """Verify WEBHOOK_BASE_URL reaches our server."""
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(callback_url) as resp:
                if 200 <= resp.status < 300:
                    return
    except Exception:
        pass
    logger.error("Cannot reach %s. Start ngrok first.", callback_url)
    raise SystemExit(1)


async def run_test_mode(start_time: int = 3600) -> None:
    """Run in test mode: generate sample history + webhook server. Use test_eventsub_webhook.py to send fake subs."""
    global _timer_seconds, _timer_history
    config = load_config()
    webhook_secret = config["webhook_secret"]
    webhook_port = config["webhook_port"]

    logger.info("TEST MODE: Generating 1 hour of sample timer history...")
    _timer_history = _generate_test_history(duration_seconds=3600, interval=10)
    _timer_seconds = _timer_history[-1]["timer_seconds"] if _timer_history else start_time
    logger.info("TEST MODE: Timer=%ds, history=%d points", _timer_seconds, len(_timer_history))
    logger.info("TEST MODE: Webhook server on port %s. Run test_eventsub_webhook.py to send fake subs.", webhook_port)
    logger.info("TEST MODE: Overlay: begathon_overlay.html (ws://localhost:6790)")

    overlay_task = asyncio.create_task(_overlay_websocket_loop())
    countdown_task = asyncio.create_task(_countdown_loop())
    runner = await run_webhook_server(webhook_port, webhook_secret)

    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        countdown_task.cancel()
        overlay_task.cancel()
        try:
            await countdown_task
        except asyncio.CancelledError:
            pass
        try:
            await overlay_task
        except asyncio.CancelledError:
            pass
        await runner.cleanup()


async def main(start_time: int, keep_state: bool = False):
    global _client_id, _broadcaster_id, _app_token, _client_secret, _timer_seconds, _timer_history
    config = load_config()
    webhook_secret = config["webhook_secret"]
    webhook_port = config["webhook_port"]
    webhook_base_url = config["webhook_base_url"]
    callback_url = f"{webhook_base_url}/eventsub" if webhook_base_url else None

    if keep_state:
        _timer_seconds, _timer_history = _load_timer_data()
        logger.info("Timer: %ds (kept from previous run)", _timer_seconds)
    else:
        _timer_seconds = start_time
        _timer_history = [{"timestamp": datetime.now(timezone.utc).isoformat(), "timer_seconds": _timer_seconds}]
        _save_timer_data()
        logger.info("Timer: %ds (start time)", _timer_seconds)

    overlay_task = asyncio.create_task(_overlay_websocket_loop())
    countdown_task = asyncio.create_task(_countdown_loop())
    runner = await run_webhook_server(webhook_port, webhook_secret)

    if callback_url:
        await _check_webhook_reachable(callback_url, webhook_port)
        app_token = await get_app_access_token(config["client_id"], config["client_secret"])
        logger.info("App token: OK")
        _app_token = app_token
        _client_id = config["client_id"]
        _client_secret = config["client_secret"]
        _broadcaster_id = config["broadcaster_id"]
        for sub_type in ("channel.subscription.message", "channel.subscription.gift"):
            await delete_existing_eventsub(
                app_token, config["client_id"], config["broadcaster_id"], sub_type
            )
            await create_eventsub_subscription(
                app_token,
                config["client_id"],
                config["broadcaster_id"],
                callback_url,
                webhook_secret,
                sub_type=sub_type,
                version="1",
            )
        logger.info("Setup complete. Waiting for Twitch verification and events.")
    else:
        logger.warning("WEBHOOK_BASE_URL not set; no subscriptions created.")
        _client_id = config["client_id"]
        _client_secret = config["client_secret"]
        _broadcaster_id = config["broadcaster_id"]

    logger.info("Server running. Overlay: begathon_overlay.html")
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        countdown_task.cancel()
        overlay_task.cancel()
        try:
            await countdown_task
        except asyncio.CancelledError:
            pass
        try:
            await overlay_task
        except asyncio.CancelledError:
            pass
        await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Begathon countdown timer - subs add time. Overlay: begathon_overlay.html"
    )
    parser.add_argument(
        "--start-time",
        type=int,
        default=3600,
        metavar="SECONDS",
        help="Initial countdown time in seconds (default: 3600 = 1 hour)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep timer state from previous run instead of resetting",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test mode: generate sample history + webhook. Use test_eventsub_webhook.py to send fake subs.",
    )
    args = parser.parse_args()
    try:
        if args.test:
            asyncio.run(run_test_mode(start_time=args.start_time))
        else:
            asyncio.run(main(start_time=args.start_time, keep_state=args.keep))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.exception("Fatal error: %s", e)
