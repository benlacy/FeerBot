#!/usr/bin/env python3
"""
Test script: sends a fake EventSub notification to your webhook (channel.subscription.message
or channel.subscription.gift). Run while begathon.py (or timerBot) is running to verify
your handler receives and logs events without a real Twitch sub.

Usage:
  python test_eventsub_webhook.py
  python test_eventsub_webhook.py --gift
  python test_eventsub_webhook.py --url http://localhost:8080/eventsub
  python test_eventsub_webhook.py --verify   # Simulate Twitch's verification challenge
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

# Twitch-style channel.subscription.message notification payload (fake data)
# Fires when a user shares their resub message on stream
FAKE_SUB_PAYLOAD = {
    "subscription": {
        "id": "test-sub-id-" + str(uuid.uuid4())[:8],
        "type": "channel.subscription.message",
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
        "message": {
            "text": "Love the stream! FevziGG",
            "emotes": [],
        },
        "cumulative_months": 6,
        "streak_months": 1,
        "duration_months": 6,
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


# Twitch-style webhook_callback_verification payload (for --verify)
def make_verify_payload():
    return {
        "challenge": "test-challenge-" + str(uuid.uuid4())[:8],
        "subscription": {
            "id": "verify-sub-id-" + str(uuid.uuid4())[:8],
            "type": "channel.subscription.message",
            "version": "1",
            "status": "webhook_callback_verification_pending",
            "cost": 0,
            "condition": {"broadcaster_user_id": _broadcaster_id()},
            "transport": {"method": "webhook", "callback": "https://example.com/eventsub"},
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Send a fake EventSub notification to your webhook")
    parser.add_argument(
        "--url",
        default=None,
        help="Webhook URL (default: http://localhost:WEBHOOK_PORT/eventsub)",
    )
    parser.add_argument("--gift", action="store_true", help="Send channel.subscription.gift instead of channel.subscription.message")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Send a webhook_callback_verification (simulate Twitch's verification challenge)",
    )
    args = parser.parse_args()

    secret = os.getenv("WEBHOOK_SECRET")
    if not secret:
        print("ERROR: WEBHOOK_SECRET not set in .env")
        return 1

    port = os.getenv("WEBHOOK_PORT", "8080")
    url = args.url or f"http://localhost:{port}/eventsub"

    if args.verify:
        payload = make_verify_payload()
        message_type = "webhook_callback_verification"
    else:
        payload = dict(FAKE_GIFT_PAYLOAD if args.gift else FAKE_SUB_PAYLOAD)
        message_type = "notification"
    # Deep copy so we don't mutate the shared subscription dict
    payload = json.loads(json.dumps(payload))

    body = json.dumps(payload)
    message_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    signature = sign_payload(message_id, timestamp, body, secret)

    headers = {
        "Content-Type": "application/json",
        "Twitch-Eventsub-Message-Type": message_type,
        "Twitch-Eventsub-Message-Id": message_id,
        "Twitch-Eventsub-Message-Timestamp": timestamp,
        "Twitch-Eventsub-Message-Signature": signature,
    }
    # Ngrok free tier shows an HTML interstitial unless we send this; then it forwards to your app
    if "ngrok" in url:
        headers["ngrok-skip-browser-warning"] = "true"

    print(f"POST {url}")
    print(f"(Webhook server should be listening on port {port} for localhost; same port as in its startup log.)")
    if args.verify:
        print(f"Verification challenge: {payload.get('challenge', '?')}")
    else:
        print(f"Fake event: {payload['subscription']['type']} -> {payload['event'].get('user_name', payload['event'].get('user_login', '?'))}")
    try:
        r = requests.post(url, data=body, headers=headers, timeout=10)
        print(f"Response: {r.status_code}")
        if r.status_code == 200:
            if args.verify:
                expected = payload.get("challenge", "")
                if r.text.strip() == expected:
                    print("OK — verification response correct (challenge echoed back).")
                else:
                    print(f"WARNING — expected challenge '{expected}' in body, got: {r.text[:100]!r}")
            else:
                print("OK — check your webhook server logs; you should see the event there.")
        else:
            print(r.text[:500])
    except requests.exceptions.ConnectionError:
        print("Connection failed. Is your webhook server running (e.g. begathon.py or timerBot)?")
        return 1
    return 0


if __name__ == "__main__":
    exit(main())
