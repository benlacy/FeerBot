#!/usr/bin/env python3
"""
EventSub Subscribe Setup - Connects a webhook, subscribes to Twitch EventSub for
channel.subscription.message (share on stream) and channel.subscription.gift on the Feer channel, and sends
the sub count to the OBS overlay (sub_count_overlay.html) via WebSocket.

Requires: WEBHOOK_BASE_URL (HTTPS, e.g. ngrok), WEBHOOK_SECRET, and .env credentials.
Run server.py (ws://localhost:6790) for overlay updates.
"""

import argparse
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

# --- Logging: messages and setup only ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

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


def _parse_twitch_timestamp(ts: str) -> datetime | None:
    """Parse Twitch RFC3339 timestamp (may include nanoseconds)."""
    if not ts:
        return None
    # Twitch uses nanoseconds; Python fromisoformat supports up to 6 (microseconds)
    s = ts.replace("Z", "+00:00")
    if len(s) > 26 and s[19] == ".":  # has fractional seconds
        frac = s[20:-6]  # digits between . and +00:00
        if len(frac) > 6:
            s = s[:20 + 6] + s[-6:]  # truncate to microseconds
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


async def _get_stream_uptime_seconds(broadcaster_id: str, app_token: str, client_id: str) -> int | None:
    """Return stream uptime in seconds, or None if offline."""
    url = f"https://api.twitch.tv/helix/streams?user_id={broadcaster_id}"
    headers = {"Authorization": f"Bearer {app_token}", "Client-Id": client_id}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                logger.warning("Streams API status %s: %s", resp.status, (await resp.text())[:200])
                return None
            data = await resp.json()
            streams = data.get("data", [])
            if not streams:
                return None
            started_at = streams[0].get("started_at")
            if not started_at:
                return None
            start = _parse_twitch_timestamp(started_at)
            if start is None:
                logger.warning("Could not parse started_at: %s", started_at[:50])
                return None
            return int((datetime.now(timezone.utc) - start).total_seconds())


async def _stream_uptime_check_loop() -> None:
    """Check stream uptime on startup and every 30 seconds; update overlay."""
    global _stream_uptime_seconds, _app_token, _client_id, _client_secret, _broadcaster_id
    while True:
        try:
            if not _client_id or not _broadcaster_id:
                await asyncio.sleep(30)
                continue
            if _app_token is None and _client_secret:
                _app_token = await get_app_access_token(_client_id, _client_secret)
            if not _app_token:
                await asyncio.sleep(30)
                continue
            uptime = await _get_stream_uptime_seconds(_broadcaster_id, _app_token, _client_id)
            _stream_uptime_seconds = uptime if uptime is not None else 0
            await _send_subcount_to_overlay()
        except Exception as e:
            logger.exception("Stream uptime check failed: %s", e)
        await asyncio.sleep(30)


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
    """
    List EventSub subscriptions and delete any subscription of sub_type for this broadcaster.
    This avoids 409 Conflict when re-running the script.
    """
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
    """
    Create one EventSub subscription for the given sub_type.
    Twitch will then POST to callback_url for verification and for events.
    """
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
    headers = {
        "Authorization": f"Bearer {app_token}",
        "Client-Id": client_id,
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=body, headers=headers) as resp:
            response_text = await resp.text()
            if resp.status == 202:
                data = json.loads(response_text)
                sub = data.get("data", [{}])[0]
                logger.info("Subscribed: %s (pending verification)", sub_type)
                return
            if resp.status == 409:
                logger.info("Subscribed: %s (already exists)", sub_type)
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


async def _check_subscription_status(
    subscription_id: str, app_token: str, client_id: str
) -> str | None:
    """Return subscription status ('enabled', 'webhook_callback_verification_pending', etc.) or None."""
    url = "https://api.twitch.tv/helix/eventsub/subscriptions"
    headers = {"Authorization": f"Bearer {app_token}", "Client-Id": client_id}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params={"subscription_id": subscription_id}, headers=headers) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            subs = data.get("data", [])
            if not subs:
                return None
            return subs[0].get("status")


async def _log_verification_result(
    subscription_id: str, sub_type: str, app_token: str, client_id: str
) -> None:
    """After a short delay, check if Twitch marked the subscription enabled and log."""
    await asyncio.sleep(3)
    status = await _check_subscription_status(subscription_id, app_token, client_id)
    if status == "enabled":
        logger.info("Verification accepted: %s is enabled", sub_type)
    elif status:
        logger.warning("Verification pending or failed: %s status=%s (check ngrok/Twitch)", sub_type, status)
    else:
        logger.warning("Could not check verification result for %s", sub_type)


async def handle_eventsub(request: web.Request, webhook_secret: str):
    """Handle Twitch EventSub: verification challenge and notifications."""
    global _sub_count
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
        body_bytes = challenge.encode("utf-8")
        sub = data.get("subscription") or {}
        sub_type = sub.get("type", "?")
        sub_id = sub.get("id")
        logger.info("Webhook: verification (%s) -> responded with challenge", sub_type)
        if sub_id and _app_token and _client_id:
            asyncio.create_task(
                _log_verification_result(sub_id, sub_type, _app_token, _client_id)
            )
        return web.Response(status=200, body=body_bytes, content_type="text/plain")

    if message_type == "notification":
        subscription = data.get("subscription", {})
        event = data.get("event", {})
        sub_type = subscription.get("type", "")
        if sub_type == "channel.subscription.message":
            # Fires when viewer shares their sub on stream (resub message / share)
            user_name = event.get("user_name") or event.get("user_login", "?")
            _sub_count += 1
            _save_sub_count()
            await _send_subcount_to_overlay()
            logger.info("Webhook: share -> %s (count=%d)", user_name, _sub_count)
        elif sub_type == "channel.subscription.gift":
            user_name = event.get("user_name") or event.get("user_login", "?")
            total = event.get("total", 1)
            _sub_count += total
            _save_sub_count()
            await _send_subcount_to_overlay()
            logger.info("Webhook: gift -> %s x%d (count=%d)", user_name, total, _sub_count)
        return web.Response(status=200)

    if message_type == "revocation":
        logger.info("Webhook: subscription revoked")
        return web.Response(status=200)

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
    """Start the local HTTP server that receives Twitch webhooks."""
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
    logger.info("Webhook server: 0.0.0.0:%s/eventsub", webhook_port)
    return runner


async def _check_webhook_reachable(callback_url: str, webhook_port: int) -> None:
    """Verify WEBHOOK_BASE_URL reaches our server (e.g. ngrok tunnel). Exit with clear message if not."""
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(callback_url) as resp:
                # Must get 2xx from our health endpoint; 502/503 means tunnel not connected
                if 200 <= resp.status < 300:
                    return
                logger.debug("Reachability check: %s returned status %s", callback_url, resp.status)
    except asyncio.TimeoutError:
        logger.debug("Reachability check: timeout")
    except Exception as e:
        logger.debug("Reachability check failed: %s", e)
    logger.error(
        "Cannot reach %s — Twitch will not be able to verify the webhook.",
        callback_url,
    )
    logger.error("Start ngrok first, then run this script. Example: ngrok http %s", webhook_port)
    raise SystemExit(1)


async def main(keep_sub_count: bool = False):
    global _client_id, _broadcaster_id, _app_token, _client_secret, _sub_count
    config = load_config()
    webhook_secret = config["webhook_secret"]
    webhook_port = config["webhook_port"]
    webhook_base_url = config["webhook_base_url"]
    callback_url = f"{webhook_base_url}/eventsub" if webhook_base_url else None

    if keep_sub_count:
        _load_sub_count()
        logger.info("Sub count: %d (kept)", _sub_count)
    else:
        _sub_count = 0
        _save_sub_count()
        logger.info("Sub count: 0 (reset)")
    overlay_task = asyncio.create_task(_overlay_websocket_loop())

    # Start server first so we're ready for Twitch's verification request
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

    stream_check_task = asyncio.create_task(_stream_uptime_check_loop())
    logger.info("Server running.")
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EventSub sub count + overlay for Feer channel")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the previous sub count from last run (default: reset to 0)",
    )
    args = parser.parse_args()
    try:
        asyncio.run(main(keep_sub_count=args.keep))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.exception("Fatal error: %s", e)
