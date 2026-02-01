#!/usr/bin/env python3
"""
TimerBot - Listens to Twitch subs and bits to control a countdown timer.
Uses EventSub webhooks to listen for subscription and cheer events.
"""

import asyncio
import logging
import json
import os
import hmac
import hashlib
from datetime import datetime, timedelta
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
OVERLAY_WS_URL = "ws://localhost:6790"
TIMER_DATA_FILE = "data/timer_data.json"
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8081"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

# Timer increment values (in seconds)
TIER_1_TIME = 3 * 60  # 3 minutes
TIER_2_TIME = 6 * 60  # 6 minutes
TIER_3_TIME = 15 * 60  # 15 minutes
GIFTED_SUB_TIME = 3 * 60  # 3 minutes per gifted sub
BITS_TIME_PER_100 = 72  # 72 seconds per 100 bits

# Graph history duration (5 hours)
GRAPH_HISTORY_HOURS = 5


class TimerBot:
    def __init__(self):
        self.timer_seconds = 0  # Current timer value in seconds
        self.total_subs = 0  # Total number of subs given
        self.timer_history = []  # History for graph (last 5 hours)
        self.ws = None
        self.webhook_app = None
        self.webhook_runner = None
        self.webhook_site = None
        self.timer_task = None
        
        # Load saved state
        self.load_timer_data()
        
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
    
    def load_timer_data(self):
        """Load timer data from JSON file."""
        data_file = Path(TIMER_DATA_FILE)
        if data_file.exists():
            try:
                with open(data_file, 'r') as f:
                    data = json.load(f)
                    self.timer_seconds = data.get('timer_seconds', 0)
                    self.total_subs = data.get('total_subs', 0)
                    self.timer_history = data.get('timer_history', [])
                    logger.info(f"Loaded timer data: {self.timer_seconds}s remaining, {self.total_subs} total subs")
            except Exception as e:
                logger.error(f"Error loading timer data: {e}")
                self.timer_seconds = 0
                self.total_subs = 0
                self.timer_history = []
        else:
            logger.info("No existing timer data found, starting fresh")
            self.timer_seconds = 0
            self.total_subs = 0
            self.timer_history = []
    
    def save_timer_data(self):
        """Save timer data to JSON file."""
        data_file = Path(TIMER_DATA_FILE)
        data_file.parent.mkdir(parents=True, exist_ok=True)
        
        data = {
            'timer_seconds': self.timer_seconds,
            'total_subs': self.total_subs,
            'timer_history': self.timer_history,
            'last_updated': datetime.now().isoformat()
        }
        
        try:
            with open(data_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Saved timer data: {self.timer_seconds}s remaining, {self.total_subs} total subs")
        except Exception as e:
            logger.error(f"Error saving timer data: {e}")
    
    def add_time(self, seconds):
        """Add time to the timer."""
        self.timer_seconds += seconds
        logger.info(f"Added {seconds}s to timer. New total: {self.timer_seconds}s")
        self.update_timer_history()
        self.save_timer_data()
        asyncio.create_task(self.send_timer_update())
    
    def update_timer_history(self):
        """Update timer history, keeping only last 5 hours."""
        now = datetime.now()
        cutoff_time = now - timedelta(hours=GRAPH_HISTORY_HOURS)
        
        # Add current entry
        self.timer_history.append({
            'timer_seconds': self.timer_seconds,
            'timestamp': now.isoformat()
        })
        
        # Remove entries older than 5 hours
        self.timer_history = [
            entry for entry in self.timer_history
            if datetime.fromisoformat(entry['timestamp']) > cutoff_time
        ]
        
        # Limit to reasonable number of entries (one per minute for 5 hours = 300 max)
        # But we'll keep more granular data, so let's limit to 1000 entries
        if len(self.timer_history) > 1000:
            self.timer_history = self.timer_history[-1000:]
    
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
            
            # Verify signature (Twitch uses message_id + timestamp + body)
            if WEBHOOK_SECRET and WEBHOOK_SECRET != "your_webhook_secret_here":
                if not self.verify_signature(message_id, timestamp, body, signature, WEBHOOK_SECRET):
                    logger.warning("Invalid webhook signature")
                    return web.Response(status=403)
                else:
                    logger.warning("Valid webhook signature")
            
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
                    # Regular subscription (tier 1, 2, 3, or prime)
                    user_name = event_data.get('user_name', 'Anonymous')
                    tier = event_data.get('tier', '1000')
                    is_gift = event_data.get('is_gift', False)
                    
                    # Only count non-gifted subs here (gifted subs are handled separately)
                    if not is_gift:
                        self.total_subs += 1
                        
                        if tier == '1000':  # Tier 1 or Prime
                            self.add_time(TIER_1_TIME)
                            logger.info(f"🎉 SUB: {user_name} subscribed (Tier 1/Prime) - Added {TIER_1_TIME}s")
                        elif tier == '2000':  # Tier 2
                            self.add_time(TIER_2_TIME)
                            logger.info(f"🎉 SUB: {user_name} subscribed (Tier 2) - Added {TIER_2_TIME}s")
                        elif tier == '3000':  # Tier 3
                            self.add_time(TIER_3_TIME)
                            logger.info(f"🎉 SUB: {user_name} subscribed (Tier 3) - Added {TIER_3_TIME}s")
                        else:
                            # Default to tier 1 for unknown tiers
                            self.add_time(TIER_1_TIME)
                            logger.info(f"🎉 SUB: {user_name} subscribed (Unknown tier {tier}) - Added {TIER_1_TIME}s")
                
                elif event_type == 'channel.subscription.gift':
                    # Gifted subscriptions
                    gifter = event_data.get('user_name', 'Anonymous')
                    total = event_data.get('total', 1)
                    tier = event_data.get('tier', '1000')
                    
                    # Add time for each gifted sub
                    time_added = total * GIFTED_SUB_TIME
                    self.total_subs += total
                    self.add_time(time_added)
                    logger.info(f"🎁 GIFTED SUBS: {gifter} gifted {total} subs (tier {tier}) - Added {time_added}s")
                
                elif event_type == 'channel.cheer':
                    # Bits donation
                    user_name = event_data.get('user_name', 'Anonymous')
                    bits = event_data.get('bits', 0)
                    
                    if bits > 0:
                        # Calculate time: 72 seconds per 100 bits
                        time_added = (bits / 100) * BITS_TIME_PER_100
                        self.add_time(time_added)
                        logger.info(f"💎 BITS: {user_name} cheered {bits} bits - Added {time_added:.1f}s")
                
                return web.Response(status=200)
            
            else:
                logger.info(f"Unknown message type: {message_type}")
                return web.Response(status=200)
                
        except Exception as e:
            logger.error(f"Error handling webhook: {e}", exc_info=True)
            return web.Response(status=500)
    
    async def start_webhook_server(self):
        """Start the webhook server."""
        self.webhook_app = web.Application()
        self.webhook_app.router.add_post('/eventsub', self.handle_eventsub)
        
        self.webhook_runner = web.AppRunner(self.webhook_app)
        await self.webhook_runner.setup()
        self.webhook_site = web.TCPSite(self.webhook_runner, 'localhost', WEBHOOK_PORT)
        await self.webhook_site.start()
        
        logger.info(f"Webhook server started on port {WEBHOOK_PORT}")
        logger.info(f"EventSub endpoint: http://localhost:{WEBHOOK_PORT}/eventsub")
    
    async def connect_websocket(self):
        """Maintain a persistent WebSocket connection to the overlay."""
        while True:
            try:
                if self.ws is None or self.ws.state != websockets.State.OPEN:
                    logger.info("Attempting to connect to WebSocket server...")
                    async with websockets.connect(OVERLAY_WS_URL) as websocket:
                        logger.info("Connected to WebSocket server")
                        self.ws = websocket
                        await self.send_timer_update()  # Send initial state
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
    
    async def send_timer_update(self):
        """Send timer update to the overlay via WebSocket."""
        if self.ws is None or self.ws.state != websockets.State.OPEN:
            logger.debug("WebSocket not connected. Message not sent.")
            return False
        
        try:
            payload = {
                "type": "timer_update",
                "timer_seconds": self.timer_seconds,
                "total_subs": self.total_subs,
                "timer_history": self.timer_history
            }
            
            await self.ws.send(json.dumps(payload))
            logger.debug(f"Sent timer update: {self.timer_seconds}s remaining, {self.total_subs} subs")
            return True
        except websockets.exceptions.ConnectionClosed:
            logger.debug("WebSocket connection closed while sending. Will reconnect.")
            self.ws = None
            return False
        except Exception as e:
            logger.error(f"Error sending message to overlay: {e}")
            return False
    
    async def timer_countdown_loop(self):
        """Background task that counts down the timer and updates the overlay."""
        last_history_update = datetime.now()
        while True:
            try:
                # Count down if timer is positive
                if self.timer_seconds > 0:
                    self.timer_seconds -= 1
                    # Ensure timer doesn't go negative
                    if self.timer_seconds < 0:
                        self.timer_seconds = 0
                    
                    # Update history every 30 seconds to show smooth graph
                    now = datetime.now()
                    if (now - last_history_update).total_seconds() >= 30:
                        self.update_timer_history()
                        self.save_timer_data()
                        last_history_update = now
                    
                    # Send update every second
                    await self.send_timer_update()
                else:
                    # Timer is at 0, just send periodic updates (every 10 seconds to reduce load)
                    await self.send_timer_update()
                    await asyncio.sleep(10)
                    continue
                
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Error in timer countdown loop: {e}")
                await asyncio.sleep(1)
    
    async def start(self):
        """Start the bot."""
        logger.info("Starting TimerBot...")
        
        # Start webhook server
        await self.start_webhook_server()
        
        # Start WebSocket connection task
        websocket_task = asyncio.create_task(self.connect_websocket())
        
        # Start timer countdown task
        self.timer_task = asyncio.create_task(self.timer_countdown_loop())
        
        # Keep running
        try:
            await asyncio.gather(websocket_task, self.timer_task)
        except KeyboardInterrupt:
            logger.info("Shutdown initiated by user")
            self.save_timer_data()


async def main():
    """Main function."""
    bot = TimerBot()
    await bot.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown initiated by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)

