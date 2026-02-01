#!/usr/bin/env python3
"""
EventSub Subscribe Setup - Connects a webhook, subscribes to Twitch EventSub for
channel.subscribe and channel.subscription.gift on the Feer channel, and sends
the sub count to the OBS overlay (sub_count_overlay.html) via WebSocket.

Every step is heavily logged so you can see exactly how the flow works.
Requires: WEBHOOK_BASE_URL (HTTPS, e.g. from ngrok), WEBHOOK_SECRET, and .env credentials.
Run server.py (WebSocket server on ws://localhost:6790) for overlay updates.
"""

import asyncio
import json
import logging
import math
import os
import hmac
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from aiohttp import web
import aiohttp
import websockets

# --- Logging: as much as possible so you can follow the flow ---
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Also print to stdout so it's visible when running
console = logging.StreamHandler()
console.setLevel(logging.DEBUG)
logger.addHandler(console)

# Silence websockets library PING/PONG keepalive spam
logging.getLogger("websockets").setLevel(logging.WARNING)

load_dotenv()

# --- Sub count state (for OBS overlay) ---
COUNT_DATA_FILE = "data/subcount_data.json"
OVERLAY_WS_URL = "ws://localhost:6790"
SUBS_PER_HOUR_GOAL = 7
_sub_count = 0
_overlay_ws = None
_stream_uptime_seconds = 0  # 0 when offline
_app_token = None
_client_id = None
_client_secret = None
_broadcaster_id = None


def _load_sub_count() -> int:
    """Load sub count from JSON file."""
    global _sub_count
    data_file = Path(COUNT_DATA_FILE)
    if data_file.exists():
        try:
            with open(data_file, "r") as f:
                data = json.load(f)
                _sub_count = data.get("sub_count", 0)
        except Exception as e:
            logger.error("Error loading count data: %s", e)
            _sub_count = 0
    else:
        _sub_count = 0
    return _sub_count


def _save_sub_count() -> None:
    """Save sub count to JSON file."""
    data_file = Path(COUNT_DATA_FILE)
    data_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(data_file, "w") as f:
            json.dump({"sub_count": _sub_count}, f, indent=2)
    except Exception as e:
        logger.error("Error saving count data: %s", e)


def _compute_sub_goal() -> tuple[int, int]:
    """Return (goal, behind). Goal = 7 subs per hour of stream. Behind = max(0, goal - count)."""
    global _stream_uptime_seconds, _sub_count
    hours_live = _stream_uptime_seconds / 3600.0
    # First hour: goal 7. Second hour: goal 14. So goal = (floor(hours) + 1) * 7
    goal = (math.floor(hours_live) + 1) * SUBS_PER_HOUR_GOAL
    behind = max(0, goal - _sub_count)
    return goal, behind


async def _get_stream_uptime_seconds(broadcaster_id: str, app_token: str, client_id: str) -> int | None:
    """Return stream uptime in seconds, or None if offline."""
    url = f"https://api.twitch.tv/helix/streams?user_id={broadcaster_id}"
    headers = {"Authorization": f"Bearer {app_token}", "Client-Id": client_id}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            streams = data.get("data", [])
            if not streams:
                return None
            started_at = streams[0].get("started_at")
            if not started_at:
                return None
            start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            return int((datetime.now(timezone.utc) - start).total_seconds())


async def _stream_uptime_check_loop() -> None:
    """Check stream uptime on startup and every 60 seconds; update overlay."""
    global _stream_uptime_seconds, _app_token, _client_id, _client_secret, _broadcaster_id
    while True:
        try:
            if _client_id and _broadcaster_id:
                if _app_token is None and _client_secret:
                    _app_token = await get_app_access_token(_client_id, _client_secret)
                if _app_token:
                    uptime = await _get_stream_uptime_seconds(_broadcaster_id, _app_token, _client_id)
                    _stream_uptime_seconds = uptime if uptime is not None else 0
                    await _send_subcount_to_overlay()
        except Exception:
            pass
        await asyncio.sleep(60)


async def _send_subcount_to_overlay() -> bool:
    """Send subcount and goal state to OBS overlay via WebSocket."""
    global _overlay_ws
    if _overlay_ws is None or _overlay_ws.state != websockets.State.OPEN:
        return False
    try:
        goal, behind = _compute_sub_goal()
        payload = {
            "type": "sub_count",
            "count": _sub_count,
            "goal": goal,
            "behind": behind,
            "goal_met": _sub_count >= goal,
        }
        await _overlay_ws.send(json.dumps(payload))
        return True
    except websockets.exceptions.ConnectionClosed:
        _overlay_ws = None
        return False
    except Exception:
        return False


# --- Configuration (with logging of what we load) ---
def load_config():
    """Load and validate config; log every value (secrets masked)."""
    logger.info("========== LOADING CONFIGURATION ==========")
    client_id = os.getenv("TWITCH_APP_CLIENT_ID")
    client_secret = os.getenv("TWITCH_APP_CLIENT_SECRET")
    broadcaster_id = os.getenv("BROADCASTER_ID")
    webhook_secret = os.getenv("WEBHOOK_SECRET")
    webhook_port = int(os.getenv("WEBHOOK_PORT", "8081"))
    # This must be your public HTTPS URL (e.g. from ngrok). Twitch will send requests here.
    webhook_base_url = os.getenv("WEBHOOK_BASE_URL", "").rstrip("/")

    logger.info("TWITCH_APP_CLIENT_ID = %s", (client_id[:8] + "..." if client_id else "NOT SET"))
    logger.info("TWITCH_APP_CLIENT_SECRET = %s", ("***" if client_secret else "NOT SET"))
    logger.info("BROADCASTER_ID (Feer channel) = %s", broadcaster_id or "NOT SET")
    logger.info("WEBHOOK_SECRET = %s", ("***" if webhook_secret else "NOT SET"))
    logger.info("WEBHOOK_PORT (local server) = %s", webhook_port)
    logger.info("WEBHOOK_BASE_URL (public HTTPS URL for Twitch to call) = %s", webhook_base_url or "NOT SET")

    if not all([client_id, client_secret, broadcaster_id, webhook_secret]):
        raise ValueError("Missing required env: TWITCH_APP_CLIENT_ID, TWITCH_APP_CLIENT_SECRET, BROADCASTER_ID, WEBHOOK_SECRET")
    if not webhook_base_url or not webhook_base_url.startswith("https://"):
        logger.warning(
            "WEBHOOK_BASE_URL must be HTTPS and publicly reachable (e.g. from ngrok). "
            "Twitch will not accept http:// or localhost. Set WEBHOOK_BASE_URL in .env and run ngrok to expose WEBHOOK_PORT."
        )

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "broadcaster_id": broadcaster_id,
        "webhook_secret": webhook_secret,
        "webhook_port": webhook_port,
        "webhook_base_url": webhook_base_url,
    }


def verify_signature(message_id: str, timestamp: str, body: str, signature: str, secret: str) -> bool:
    """
    Verify Twitch EventSub message signature (HMAC-SHA256).
    Twitch uses: HMAC(secret, message_id + timestamp + body) — not body alone.
    """
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
    """
    Get an app access token using client_credentials.
    EventSub subscription creation with webhooks requires an APP access token (not user token).
    """
    logger.info("========== REQUESTING APP ACCESS TOKEN ==========")
    url = "https://id.twitch.tv/oauth2/token"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }
    logger.info("POST %s (grant_type=client_credentials)", url)
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=payload) as resp:
            text = await resp.text()
            logger.info("OAuth response status = %s", resp.status)
            logger.debug("OAuth response body = %s", text[:500])
            if resp.status != 200:
                logger.error("Failed to get app token: %s", text)
                raise RuntimeError(f"OAuth failed: {text}")
            data = json.loads(text)
            token = data["access_token"]
            logger.info("App access token obtained (length=%d)", len(token))
            return token


async def delete_existing_eventsub(
    app_token: str, client_id: str, broadcaster_id: str, sub_type: str
) -> None:
    """
    List EventSub subscriptions and delete any subscription of sub_type for this broadcaster.
    This avoids 409 Conflict when re-running the script.
    """
    logger.info("========== CHECKING EXISTING EVENTSUB SUBSCRIPTIONS (%s) ==========", sub_type)
    url = "https://api.twitch.tv/helix/eventsub/subscriptions"
    headers = {
        "Authorization": f"Bearer {app_token}",
        "Client-Id": client_id,
    }
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
                logger.info("Deleting existing subscription: id=%s type=%s", sub_id, sub.get("type"))
                async with session.delete(url, params={"id": sub_id}, headers=headers) as del_resp:
                    if del_resp.status in (204, 200):
                        logger.info("Deleted subscription %s", sub_id)
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
    """
    Create one EventSub subscription for the given sub_type.
    Twitch will then POST to callback_url for verification and for events.
    """
    logger.info("========== CREATING EVENTSUB SUBSCRIPTION (%s) ==========", sub_type)
    url = "https://api.twitch.tv/helix/eventsub/subscriptions"
    body = {
        "type": sub_type,
        "version": version,
        "condition": {"broadcaster_user_id": broadcaster_id},
        "transport": {
            "method": "webhook",
            "callback": callback_url,
            "secret": secret,
        },
    }
    logger.info("POST %s", url)
    logger.info("Request body: %s", json.dumps(body, indent=2))
    headers = {
        "Authorization": f"Bearer {app_token}",
        "Client-Id": client_id,
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=body, headers=headers) as resp:
            response_text = await resp.text()
            logger.info("EventSub API response status = %s", resp.status)
            logger.info("EventSub API response body = %s", response_text[:1000])
            if resp.status == 202:
                data = json.loads(response_text)
                sub = data.get("data", [{}])[0]
                logger.info("Subscription created: id=%s status=%s", sub.get("id"), sub.get("status"))
                logger.info(
                    "Twitch will send a verification request to your callback; "
                    "once we respond with the challenge, status will become 'enabled'."
                )
                return
            if resp.status == 409:
                logger.info(
                    "Subscription already exists (same type+condition). "
                    "Continuing — webhook server will still receive events."
                )
                return
            logger.error("EventSub subscription failed: %s", response_text)
            if resp.status == 401:
                logger.error("Check that you used an APP access token (client_credentials), not a user token.")
            if resp.status == 400 or "authorization" in response_text.lower():
                logger.error(
                    "%s requires scope channel:read:subscriptions. "
                    "Re-authorize the app with that scope for the Feer account.",
                    sub_type,
                )
            raise RuntimeError(f"EventSub create failed: {response_text}")


async def handle_eventsub(request: web.Request, webhook_secret: str):
    """
    Handle incoming Twitch EventSub requests: verification challenge and notifications.
    Log everything so you can see the flow.
    """
    global _sub_count
    logger.info("========== INCOMING WEBHOOK REQUEST ==========")
    logger.info("Method: %s", request.method)
    logger.info("Path: %s", request.path)
    # Log relevant headers (Twitch sends these)
    for name in ("Twitch-Eventsub-Message-Id", "Twitch-Eventsub-Message-Type", "Twitch-Eventsub-Message-Signature", "Twitch-Eventsub-Message-Timestamp"):
        val = request.headers.get(name, "")
        logger.info("Header %s: %s", name, val[:80] + "..." if len(val) > 80 else val)

    body = await request.text()
    logger.info("Body length: %d bytes", len(body))
    logger.debug("Body (first 500 chars): %s", body[:500])

    message_id = request.headers.get("Twitch-Eventsub-Message-Id", "")
    timestamp = request.headers.get("Twitch-Eventsub-Message-Timestamp", "")
    signature = request.headers.get("Twitch-Eventsub-Message-Signature", "")
    if webhook_secret and not verify_signature(message_id, timestamp, body, signature, webhook_secret):
        logger.warning("Signature verification FAILED - rejecting request")
        return web.Response(status=403, text="Forbidden")
    logger.info("Signature verification OK")

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON: %s", e)
        return web.Response(status=400, text="Bad Request")

    message_type = request.headers.get("Twitch-Eventsub-Message-Type", "")
    logger.info("Message type: %s", message_type)

    if message_type == "webhook_callback_verification":
        challenge = data.get("challenge", "")
        logger.info("Twitch sent a verification challenge. We must respond with this challenge so Twitch knows we own the URL.")
        logger.info("Challenge (first 50 chars): %s...", challenge[:50])
        logger.info("Responding with 200 OK and body = challenge.")
        return web.Response(text=challenge)

    if message_type == "notification":
        subscription = data.get("subscription", {})
        event = data.get("event", {})
        sub_type = subscription.get("type", "")
        logger.info("Notification for subscription type: %s", sub_type)
        logger.info("Subscription id: %s", subscription.get("id"))
        logger.info("Event payload: %s", json.dumps(event, indent=2))
        if sub_type == "channel.subscribe":
            user_name = event.get("user_name") or event.get("user_login", "?")
            tier = event.get("tier", "?")
            is_gift = event.get("is_gift", False)
            logger.info(">>> channel.subscribe: user=%s tier=%s is_gift=%s", user_name, tier, is_gift)
            if not is_gift:
                _sub_count += 1
                _save_sub_count()
                await _send_subcount_to_overlay()
        elif sub_type == "channel.subscription.gift":
            user_name = event.get("user_name") or event.get("user_login", "?")
            total = event.get("total", 1)
            cumulative_total = event.get("cumulative_total", "?")
            is_anonymous = event.get("is_anonymous", False)
            logger.info(
                ">>> channel.subscription.gift: user=%s total=%s cumulative_total=%s is_anonymous=%s",
                user_name, total, cumulative_total, is_anonymous,
            )
            _sub_count += total
            _save_sub_count()
            await _send_subcount_to_overlay()
        logger.info("Responding with 200 OK (so Twitch does not retry).")
        return web.Response(status=200)

    if message_type == "revocation":
        logger.info("Twitch sent a revocation (subscription was revoked). Payload: %s", json.dumps(data, indent=2))
        return web.Response(status=200)

    logger.info("Unknown message type; responding 200 anyway")
    return web.Response(status=200)


async def _overlay_websocket_loop() -> None:
    """Maintain WebSocket connection to overlay server and send subcount updates."""
    global _overlay_ws
    while True:
        try:
            if _overlay_ws is None or _overlay_ws.state != websockets.State.OPEN:
                async with websockets.connect(OVERLAY_WS_URL) as ws:
                    _overlay_ws = ws
                    await _send_subcount_to_overlay()
                    await ws.wait_closed()
                    _overlay_ws = None
            else:
                await asyncio.sleep(1)
        except websockets.exceptions.ConnectionClosed:
            _overlay_ws = None
        except Exception:
            _overlay_ws = None
        await asyncio.sleep(2)


@web.middleware
async def log_all_requests(request: web.Request, handler):
    """Log every request as soon as it hits the server (so you can confirm traffic reaches this process)."""
    logger.info(">>> REQUEST RECEIVED: %s %s", request.method, request.path)
    try:
        return await handler(request)
    except Exception as e:
        logger.exception("Handler error: %s", e)
        raise


async def run_webhook_server(webhook_port: int, webhook_secret: str):
    """Start the local HTTP server that receives Twitch webhooks."""
    logger.info("========== STARTING WEBHOOK SERVER ==========")
    logger.info("Binding to 0.0.0.0:%s (so that ngrok or other tunnel can forward to this process)", webhook_port)
    app = web.Application(middlewares=[log_all_requests])
    app.router.add_post("/eventsub", lambda r: handle_eventsub(r, webhook_secret))

    async def health(request):
        return web.Response(
            text=f"EventSub webhook server OK (port {webhook_port}). POST /eventsub for Twitch. Sub count: {_sub_count}\n"
        )

    app.router.add_get("/eventsub", health)
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", webhook_port)
    await site.start()
    logger.info("Webhook server is listening on http://0.0.0.0:%s/eventsub", webhook_port)
    logger.info("Twitch will send POST requests to: <WEBHOOK_BASE_URL>/eventsub")
    return runner


async def main():
    logger.info("========== EVENTSUB SUBSCRIBE SETUP (channel.subscribe + channel.subscription.gift for Feer) ==========")
    config = load_config()
    webhook_secret = config["webhook_secret"]
    webhook_port = config["webhook_port"]
    webhook_base_url = config["webhook_base_url"]
    callback_url = f"{webhook_base_url}/eventsub" if webhook_base_url else None

    # Load sub count and start overlay WebSocket task
    _load_sub_count()
    overlay_task = asyncio.create_task(_overlay_websocket_loop())

    # Start server first so we're ready for Twitch's verification request
    runner = await run_webhook_server(webhook_port, webhook_secret)

    if callback_url:
        logger.info("========== STEP 1: Get app access token ==========")
        app_token = await get_app_access_token(config["client_id"], config["client_secret"])
        _app_token = app_token
        _client_id = config["client_id"]
        _client_secret = config["client_secret"]
        _broadcaster_id = config["broadcaster_id"]
        for sub_type in ("channel.subscribe", "channel.subscription.gift"):
            logger.info("========== Remove existing %s (if any) ==========", sub_type)
            await delete_existing_eventsub(
                app_token, config["client_id"], config["broadcaster_id"], sub_type
            )
            logger.info("========== Create EventSub subscription: %s ==========", sub_type)
            await create_eventsub_subscription(
                app_token,
                config["client_id"],
                config["broadcaster_id"],
                callback_url,
                webhook_secret,
                sub_type=sub_type,
                version="1",
            )
        logger.info("Setup complete. Twitch will POST to your callback; watch logs for verification and events.")
    else:
        logger.warning("WEBHOOK_BASE_URL not set. Only running webhook server (no subscription created).")
        logger.info("Set WEBHOOK_BASE_URL to your public HTTPS URL (e.g. https://xxxx.ngrok.io) and restart to create the subscription.")
        _client_id = config["client_id"]
        _client_secret = config["client_secret"]
        _broadcaster_id = config["broadcaster_id"]

    # Start stream uptime check (on startup + every 60s)
    stream_check_task = asyncio.create_task(_stream_uptime_check_loop())

    logger.info("Server running. Press Ctrl+C to stop.")
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        stream_check_task.cancel()
        overlay_task.cancel()
        try:
            await stream_check_task
        except asyncio.CancelledError:
            pass
        try:
            await overlay_task
        except asyncio.CancelledError:
            pass
        await runner.cleanup()
        logger.info("Webhook server stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.exception("Fatal error: %s", e)
