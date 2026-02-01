#!/usr/bin/env python3
"""
Test script: sends a fake EventSub notification to your webhook (channel.subscribe
or channel.subscription.gift). Run while eventsub_subscribe_setup.py (or timerBot)
is running to verify your handler receives and logs events without a real Twitch sub.

Usage:
  python test_eventsub_webhook.py
  python test_eventsub_webhook.py --gift
  python test_eventsub_webhook.py --url http://localhost:8080/eventsub
"""

import argparse
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

def _broadcaster_id():
    return os.getenv("BROADCASTER_ID", "147306920")

# Twitch-style channel.subscribe notification payload (fake data)
FAKE_SUB_PAYLOAD = {
    "subscription": {
        "id": "test-sub-id-" + str(uuid.uuid4())[:8],
        "type": "channel.subscribe",
        "version": "1",
        "status": "enabled",
        "cost": 0,
        "condition": {"broadcaster_user_id": _broadcaster_id()},
        "transport": {"method": "webhook", "callback": "https://example.com/eventsub"},
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    },
    "event": {
        "user_id": "12345678",
        "user_login": "test_viewer",
        "user_name": "TestViewer",
        "broadcaster_user_id": _broadcaster_id(),
        "broadcaster_user_login": "feer",
        "broadcaster_user_name": "Feer",
        "tier": "1000",
        "is_gift": False,
    },
}

# Twitch-style channel.subscription.gift notification payload (fake data)
FAKE_GIFT_PAYLOAD = {
    "subscription": {
        "id": "test-gift-id-" + str(uuid.uuid4())[:8],
        "type": "channel.subscription.gift",
        "version": "1",
        "status": "enabled",
        "cost": 0,
        "condition": {"broadcaster_user_id": _broadcaster_id()},
        "transport": {"method": "webhook", "callback": "https://example.com/eventsub"},
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    },
    "event": {
        "user_id": "87654321",
        "user_login": "gift_giver",
        "user_name": "GiftGiver",
        "broadcaster_user_id": _broadcaster_id(),
        "broadcaster_user_login": "feer",
        "broadcaster_user_name": "Feer",
        "total": 3,
        "tier": "1000",
        "cumulative_total": 42,
        "is_anonymous": False,
    },
}


def sign_payload(message_id: str, timestamp: str, body: str, secret: str) -> str:
    """Same signature Twitch uses: HMAC-SHA256(secret, message_id + timestamp + body)."""
    hmac_message = message_id + timestamp + body
    sig = hmac.new(secret.encode("utf-8"), hmac_message.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def main():
    parser = argparse.ArgumentParser(description="Send a fake EventSub notification to your webhook")
    parser.add_argument(
        "--url",
        default=None,
        help="Webhook URL (default: http://localhost:WEBHOOK_PORT/eventsub)",
    )
    parser.add_argument("--gift", action="store_true", help="Send a fake gifted sub instead")
    args = parser.parse_args()

    secret = os.getenv("WEBHOOK_SECRET")
    if not secret:
        print("ERROR: WEBHOOK_SECRET not set in .env")
        return 1

    port = os.getenv("WEBHOOK_PORT", "8080")
    url = args.url or f"http://localhost:{port}/eventsub"

    payload = dict(FAKE_GIFT_PAYLOAD if args.gift else FAKE_SUB_PAYLOAD)
    # Deep copy so we don't mutate the shared subscription dict
    payload = json.loads(json.dumps(payload))

    body = json.dumps(payload)
    message_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    signature = sign_payload(message_id, timestamp, body, secret)

    headers = {
        "Content-Type": "application/json",
        "Twitch-Eventsub-Message-Type": "notification",
        "Twitch-Eventsub-Message-Id": message_id,
        "Twitch-Eventsub-Message-Timestamp": timestamp,
        "Twitch-Eventsub-Message-Signature": signature,
    }
    # Ngrok free tier shows an HTML interstitial unless we send this; then it forwards to your app
    if "ngrok" in url:
        headers["ngrok-skip-browser-warning"] = "true"

    print(f"POST {url}")
    print(f"(Webhook server should be listening on port {port} for localhost; same port as in its startup log.)")
    print(f"Fake event: {payload['subscription']['type']} -> {payload['event'].get('user_name', payload['event'].get('user_login', '?'))}")
    try:
        r = requests.post(url, data=body, headers=headers, timeout=10)
        print(f"Response: {r.status_code}")
        if r.status_code == 200:
            print("OK — check your webhook server logs; you should see the event there.")
        else:
            print(r.text[:500])
    except requests.exceptions.ConnectionError:
        print("Connection failed. Is your webhook server running (e.g. eventsub_subscribe_setup.py or timerBot)?")
        return 1
    return 0


if __name__ == "__main__":
    exit(main())
