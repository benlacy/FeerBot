#!/usr/bin/env python3
"""
Milkathon - Goal tracker that advances through milestones when subscriptions are received.
Uses EventSub webhooks for channel.subscription.message (share on stream) and
channel.subscription.gift. Sends goal state to milkathon_overlay.html via WebSocket.

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

# --- Milkathon goals: (sub_count, goal_text) sorted by sub_count ---
MILKATHON_GOALS = [
    (1, "Bring in glass"),
    (2, "Pour milk"),
    (3, "Pour more milk"),
    (4, "Pour morre milk"),
    (5, "Drink some %2 milk"),
    (6, "Activate Milk Music"),
    (7, "1% Milk Jug and New cup"),
    (8, "Pour milk"),
    (9, "Pour some milk"),
    (10, "Pour the rest"),
    (11, "Whole Milk and New Cup"),
    (12, "Pour the Whole Milk"),
    (13, "Pour more Whole Milk"),
    (14, "Pour the rest of the Whole Milk"),
    (15, "Drink some whole milk"),
    (16, "How Milk is Made (video)"),
    (20, "Milk Trailer"),
    (25, "Milk Jugs Have dimples"),
    (30, "Raw Milk"),
    (35, "Clean Milk"),
    (40, "Milk Explosion"),
    (45, "Yes Milk"),
    (55, "Milkman"),
    (60, "Color Changing Milk"),
    (61, "Stop subscribing I am out of things"),
    (67, "Seriously no other ideas"),
    (69, "There's not a secret additional idea"),
    (1439, "You guys are ridiculous for letting it get this far")
]

# --- State ---
MILKATHON_DATA_FILE = "data/milkathon_data.json"
OVERLAY_WS_URL = "ws://localhost:6790"
TIMER_RESET_SECONDS = 3 * 60  # 3 minutes, resets on each sub

_total_subs = 0
_timer_seconds = 0
_achieved_goals: list[tuple[int, str]] = []  # [(subs, text), ...]
_overlay_ws = None
_app_token = None
_client_id = None
_client_secret = None
_broadcaster_id = None


def _get_next_goal() -> tuple[str | None, int | None, int]:
    """
    Return (next_goal_text, subs_required_for_goal, subs_remaining).
    subs_required_for_goal = total subs needed to hit the goal.
    subs_remaining = how many more subs until we get there.
    """
    for threshold, text in MILKATHON_GOALS:
        if _total_subs < threshold:
            return text, threshold, threshold - _total_subs
    return None, None, 0  # All goals achieved


def _process_new_subs(count: int) -> None:
    """Add subs and update achieved goals."""
    global _total_subs, _achieved_goals
    _total_subs += count
    achieved_texts = {t for _, t in _achieved_goals}
    for threshold, text in MILKATHON_GOALS:
        if _total_subs >= threshold and text not in achieved_texts:
            _achieved_goals.append((threshold, text))


def _load_data() -> tuple[int, list[tuple[int, str]], int]:
    """Load state from JSON file."""
    global _total_subs, _achieved_goals, _timer_seconds
    data_file = Path(MILKATHON_DATA_FILE)
    if data_file.exists():
        try:
            with open(data_file, "r") as f:
                data = json.load(f)
                _total_subs = data.get("total_subs", 0)
                _timer_seconds = data.get("timer_seconds", TIMER_RESET_SECONDS)
                raw = data.get("achieved_goals", [])
                # Support both old format [str] and new format [{"subs": n, "text": s}]
                _achieved_goals = []
                for i, item in enumerate(raw):
                    if isinstance(item, dict):
                        _achieved_goals.append((item["subs"], item["text"]))
                    elif isinstance(item, str) and i < len(MILKATHON_GOALS):
                        thresh, _ = MILKATHON_GOALS[i]
                        _achieved_goals.append((thresh, item))
        except Exception as e:
            logger.error("Error loading milkathon data: %s", e)
            _total_subs = 0
            _achieved_goals = []
            _timer_seconds = TIMER_RESET_SECONDS
    else:
        _timer_seconds = TIMER_RESET_SECONDS
    return _total_subs, _achieved_goals, _timer_seconds


def _save_data() -> None:
    """Save state to JSON file."""
    data_file = Path(MILKATHON_DATA_FILE)
    data_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(data_file, "w") as f:
            json.dump({
                "total_subs": _total_subs,
                "timer_seconds": _timer_seconds,
                "achieved_goals": [{"subs": s, "text": t} for s, t in _achieved_goals],
            }, f, indent=2)
    except Exception as e:
        logger.error("Error saving milkathon data: %s", e)


async def _send_to_overlay() -> bool:
    """Send goal state to OBS overlay via WebSocket."""
    global _overlay_ws
    if _overlay_ws is None or _overlay_ws.state != websockets.State.OPEN:
        return False
    try:
        next_text, subs_for_goal, subs_remaining = _get_next_goal()
        payload = {
            "type": "milkathon_goals",
            "total_subs": _total_subs,
            "next_goal": next_text,
            "subs_required": subs_for_goal,
            "subs_remaining": subs_remaining,
            "achieved_goals": [{"subs": s, "text": t} for s, t in _achieved_goals],
            "timer_seconds": _timer_seconds,
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
    global _total_subs, _achieved_goals, _timer_seconds
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
            _process_new_subs(1)
            _timer_seconds = TIMER_RESET_SECONDS
            _save_data()
            await _send_to_overlay()
            logger.info("Webhook: share -> %s, total_subs=%d", user_name, _total_subs)
        elif sub_type == "channel.subscription.gift":
            user_name = event.get("user_name") or event.get("user_login", "?")
            total = event.get("total", 1)
            _process_new_subs(total)
            _timer_seconds = TIMER_RESET_SECONDS
            _save_data()
            await _send_to_overlay()
            logger.info("Webhook: gift -> %s x%d, total_subs=%d", user_name, total, _total_subs)
        return web.Response(status=200)

    if message_type == "revocation":
        logger.info("Webhook: subscription revoked")
        return web.Response(status=200)

    return web.Response(status=200)


async def _overlay_websocket_loop() -> None:
    """Maintain WebSocket connection to overlay server and send goal updates."""
    global _overlay_ws
    while True:
        try:
            if _overlay_ws is None or _overlay_ws.state != websockets.State.OPEN:
                async with websockets.connect(OVERLAY_WS_URL) as ws:
                    _overlay_ws = ws
                    await _send_to_overlay()
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
    """Count down timer every second and send update to overlay."""
    global _timer_seconds
    while True:
        try:
            if _timer_seconds > 0:
                _timer_seconds -= 1
            _save_data()
            await _send_to_overlay()
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
            text=f"Milkathon webhook OK (port {webhook_port}). Subs: {_total_subs}\n"
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


async def run_test_mode() -> None:
    """Run in test mode: webhook server only. Use test_eventsub_webhook.py to send fake subs."""
    global _total_subs, _achieved_goals, _timer_seconds
    config = load_config()
    webhook_secret = config["webhook_secret"]
    webhook_port = config["webhook_port"]

    _total_subs, _achieved_goals, _timer_seconds = _load_data()
    logger.info("TEST MODE: Subs=%d, achieved=%d goals, timer=%ds", _total_subs, len(_achieved_goals), _timer_seconds)
    logger.info("TEST MODE: Webhook server on port %s. Run test_eventsub_webhook.py to send fake subs.", webhook_port)
    logger.info("TEST MODE: Overlay: milkathon_overlay.html (ws://localhost:6790)")

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


async def main(keep_state: bool = False):
    global _client_id, _broadcaster_id, _app_token, _client_secret, _total_subs, _achieved_goals, _timer_seconds
    config = load_config()
    webhook_secret = config["webhook_secret"]
    webhook_port = config["webhook_port"]
    webhook_base_url = config["webhook_base_url"]
    callback_url = f"{webhook_base_url}/eventsub" if webhook_base_url else None

    if keep_state:
        _total_subs, _achieved_goals, _timer_seconds = _load_data()
        logger.info("Subs: %d, achieved: %d goals, timer=%ds (kept from previous run)", _total_subs, len(_achieved_goals), _timer_seconds)
    else:
        _total_subs = 0
        _achieved_goals = []
        _timer_seconds = TIMER_RESET_SECONDS
        _save_data()
        logger.info("Subs: 0, timer: 5:00 (fresh start)")

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

    logger.info("Server running. Overlay: milkathon_overlay.html")
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
        description="Milkathon goal tracker - subs unlock milk milestones. Overlay: milkathon_overlay.html"
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep sub count and achieved goals from previous run instead of resetting",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test mode: webhook only. Use test_eventsub_webhook.py to send fake subs.",
    )
    args = parser.parse_args()
    try:
        if args.test:
            asyncio.run(run_test_mode())
        else:
            asyncio.run(main(keep_state=args.keep))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.exception("Fatal error: %s", e)
