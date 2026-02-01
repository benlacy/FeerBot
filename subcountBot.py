#!/usr/bin/env python3
"""
SubCountBot - Simple bot that counts subscriptions (regular and gifted) and prints the total.
Uses EventSub webhooks to listen for channel.subscribe and channel.subscription.gift events.
"""

import asyncio
import logging
import json
import os
import hmac
import hashlib
from pathlib import Path
from dotenv import load_dotenv
from aiohttp import web
import websockets

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Configuration
COUNT_DATA_FILE = "data/subcount_data.json"
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8081"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
OVERLAY_WS_URL = "ws://localhost:6790"


class SubCountBot:
    def __init__(self):
        self.sub_count = 0  # Total number of subs counted while bot is active
        self.ws = None  # WebSocket connection to overlay
        
        # Load saved count (optional persistence)
        self.load_count()
        
    def verify_signature(self, message_id, timestamp, payload, signature, secret):
        """Verify Twitch EventSub signature: HMAC-SHA256(secret, message_id + timestamp + body)."""
        if not signature or not secret or not message_id or not timestamp:
            return False
        hmac_message = message_id + timestamp + payload
        expected = 'sha256=' + hmac.new(
            secret.encode('utf-8'),
            hmac_message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    
    def load_count(self):
        """Load sub count from JSON file (optional persistence)."""
        data_file = Path(COUNT_DATA_FILE)
        if data_file.exists():
            try:
                with open(data_file, 'r') as f:
                    data = json.load(f)
                    self.sub_count = data.get('sub_count', 0)
                    logger.info(f"Loaded sub count: {self.sub_count} subs")
            except Exception as e:
                logger.error(f"Error loading count data: {e}")
                self.sub_count = 0
        else:
            logger.info("Starting fresh - sub count: 0")
            self.sub_count = 0
    
    def save_count(self):
        """Save sub count to JSON file (optional persistence)."""
        data_file = Path(COUNT_DATA_FILE)
        data_file.parent.mkdir(parents=True, exist_ok=True)
        
        data = {
            'sub_count': self.sub_count
        }
        
        try:
            with open(data_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Saved sub count: {self.sub_count}")
        except Exception as e:
            logger.error(f"Error saving count data: {e}")
    
    async def increment_count(self, amount=1):
        """Increment the sub count and print it."""
        self.sub_count += amount
        logger.info(f"📊 SUB COUNT: {self.sub_count} total subs")
        print(f"📊 SUB COUNT: {self.sub_count} total subs")
        self.save_count()
        # Send update to overlay
        await self.send_subcount_update()
    
    async def handle_eventsub(self, request):
        """Handle EventSub webhook requests."""
        try:
            # Get headers
            message_id = request.headers.get('Twitch-Eventsub-Message-Id', '')
            timestamp = request.headers.get('Twitch-Eventsub-Message-Timestamp', '')
            signature = request.headers.get('Twitch-Eventsub-Message-Signature', '')
            message_type = request.headers.get('Twitch-Eventsub-Message-Type', '')
            
            # Get raw body
            body = await request.text()
            
            # Verify signature
            if WEBHOOK_SECRET and WEBHOOK_SECRET != "your_webhook_secret_here":
                if not self.verify_signature(message_id, timestamp, body, signature, WEBHOOK_SECRET):
                    logger.warning("Invalid webhook signature")
                    return web.Response(status=403)
            
            # Parse JSON
            data = json.loads(body)
            
            # Handle different message types
            if message_type == 'webhook_callback_verification':
                # Return challenge for verification
                challenge = data.get('challenge', '')
                logger.info(f"Webhook verification challenge: {challenge}")
                return web.Response(text=challenge)
            
            elif message_type == 'notification':
                # Handle actual events
                event_type = data.get('subscription', {}).get('type', '')
                event_data = data.get('event', {})
                
                logger.info(f"Received EventSub notification: {event_type}")
                
                if event_type == 'channel.subscribe':
                    # Regular subscription
                    user_name = event_data.get('user_name', 'Anonymous')
                    tier = event_data.get('tier', '1000')
                    is_gift = event_data.get('is_gift', False)
                    
                    # Only count non-gifted subs here (gifted subs are handled separately)
                    if not is_gift:
                        await self.increment_count(1)
                        logger.info(f"🎉 {user_name} subscribed (Tier {tier})")
                
                elif event_type == 'channel.subscription.gift':
                    # Gifted subscriptions
                    gifter = event_data.get('user_name', 'Anonymous')
                    total = event_data.get('total', 1)
                    tier = event_data.get('tier', '1000')
                    
                    # Count all gifted subs
                    await self.increment_count(total)
                    logger.info(f"🎁 {gifter} gifted {total} subs (Tier {tier})")
                
                return web.Response(status=200)
            
            else:
                logger.info(f"Unknown message type: {message_type}")
                return web.Response(status=200)
                
        except Exception as e:
            logger.error(f"Error handling webhook: {e}", exc_info=True)
            return web.Response(status=500)
    
    async def connect_websocket(self):
        """Maintain a persistent WebSocket connection to the overlay."""
        while True:
            try:
                if self.ws is None or self.ws.state != websockets.State.OPEN:
                    logger.info("Attempting to connect to WebSocket server...")
                    async with websockets.connect(OVERLAY_WS_URL) as websocket:
                        logger.info("Connected to WebSocket server")
                        self.ws = websocket
                        await self.send_subcount_update()  # Send initial state
                        await websocket.wait_closed()
                        self.ws = None
                        logger.info("WebSocket connection closed")
                else:
                    await asyncio.sleep(1)
            except websockets.exceptions.ConnectionClosed:
                self.ws = None
                logger.info("Connection closed by server")
            except Exception as e:
                self.ws = None
                logger.error(f"WebSocket connection error: {e}")
            
            await asyncio.sleep(2)
    
    async def send_subcount_update(self):
        """Send subcount update to the overlay via WebSocket."""
        if self.ws is None or self.ws.state != websockets.State.OPEN:
            logger.debug("WebSocket not connected. Message not sent.")
            return False
        
        try:
            payload = {
                "type": "sub_count",
                "count": self.sub_count
            }
            
            await self.ws.send(json.dumps(payload))
            logger.debug(f"Sent subcount update: {self.sub_count} subs")
            return True
        except websockets.exceptions.ConnectionClosed:
            logger.debug("WebSocket connection closed while sending. Will reconnect.")
            self.ws = None
            return False
        except Exception as e:
            logger.error(f"Error sending message to overlay: {e}")
            return False
    
    async def start_webhook_server(self):
        """Start the webhook server."""
        webhook_app = web.Application()
        webhook_app.router.add_post('/eventsub', self.handle_eventsub)
        
        # Add a simple health check endpoint
        async def health_check(request):
            return web.Response(text=f"SubCountBot running - Current count: {self.sub_count}\n")
        
        webhook_app.router.add_get('/', health_check)
        webhook_app.router.add_get('/eventsub', health_check)
        
        webhook_runner = web.AppRunner(webhook_app)
        await webhook_runner.setup()
        webhook_site = web.TCPSite(webhook_runner, '0.0.0.0', WEBHOOK_PORT)
        await webhook_site.start()
        
        logger.info(f"Webhook server started on port {WEBHOOK_PORT}")
        logger.info(f"EventSub endpoint: http://0.0.0.0:{WEBHOOK_PORT}/eventsub")
        logger.info(f"Current sub count: {self.sub_count}")
        print(f"📊 Current sub count: {self.sub_count}")
        
        return webhook_runner
    
    async def start(self):
        """Start the bot."""
        logger.info("Starting SubCountBot...")
        
        # Start webhook server
        runner = await self.start_webhook_server()
        
        # Start WebSocket connection task
        websocket_task = asyncio.create_task(self.connect_websocket())
        
        # Keep running
        try:
            await websocket_task
        except KeyboardInterrupt:
            logger.info("Shutdown initiated by user")
            self.save_count()
            await runner.cleanup()


async def main():
    """Main function."""
    bot = SubCountBot()
    await bot.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown initiated by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
