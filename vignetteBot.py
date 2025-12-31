from baseBot import BaseBot
from dotenv import load_dotenv
import aiohttp
import logging
import asyncio
import json
from twitchio.ext import eventsub
import os

logger = logging.getLogger(__name__)

class VignetteBot(BaseBot):
    def __init__(self, overlay_ws_url: str = "ws://localhost:6790"):
        super().__init__(overlay_ws_url=overlay_ws_url)
        # Vignette settings
        self.max_viewers = 1000  # Maximum viewers for 100% vignette
        self.update_interval = 15  # Update every 15 seconds (safest fast poll, avoid Twitch API rate limit)
        self.current_viewer_count = 0
        self.vignette_task = None
        
        # Bounce settings
        self.bounce_active = False
        self.bounce_end_time = 0
        self.bounce_duration = 30  # 30 seconds of bouncing
        
    #     # EventSub settings
    #     self.webhook_port = int(os.getenv("WEBHOOK_PORT", "8080"))
    #     self.webhook_secret = os.getenv("WEBHOOK_SECRET", "your_webhook_secret_here")
    #     self.webhook_url = os.getenv("WEBHOOK_URL", f"http://localhost:{self.webhook_port}")
    #     self.eventsub_client = None
    
    # async def setup_eventsub(self):
    #     """Initialize EventSub client and subscribe to events."""
    #     try:
    #         # Initialize EventSub client
    #         self.eventsub_client = eventsub.EventSubClient(
    #             client_id=self.client_id,
    #             client_secret=self.client_secret,
    #             callback_url=self.webhook_url,
    #             webhook_secret=self.webhook_secret
    #         )
            
    #         # Get broadcaster ID for subscriptions
    #         broadcaster_id = await self.get_broadcaster_id()
    #         if not broadcaster_id:
    #             logger.error("Could not get broadcaster ID for EventSub")
    #             return
            
    #         # Subscribe to sub gift events
    #         await self.eventsub_client.subscribe_channel_subscription_gift(
    #             broadcaster_id=broadcaster_id,
    #             callback_url=f"{self.webhook_url}/eventsub"
    #         )
            
    #         logger.info("EventSub client initialized and subscribed to sub gift events")
            
    #     except Exception as e:
    #         logger.error(f"Failed to setup EventSub: {e}")
    
    # async def handle_sub_gift(self, event_data):
    #     """Handle sub gift event from EventSub."""
    #     try:
    #         gifter_name = event_data.get('user_name', 'Anonymous')
    #         total_gifted = event_data.get('total', 1)
    #         tier = event_data.get('tier', '1000')
            
    #         logger.info(f"Sub gift detected: {gifter_name} gifted {total_gifted} subs (tier {tier})")
            
    #         # Trigger bounce for each sub gifted
    #         for _ in range(total_gifted):
    #             await self.trigger_bounce()
                
        # except Exception as e:
        #     logger.error(f"Error handling sub gift event: {e}")
    
    async def event_message(self, message):
        if message.echo:
            return
        
        # Sub gift detection is now handled by EventSub webhooks
        
        # Check for viewer count command
        if message.content.lower() in ['!viewers', '!viewercount', '!count']:
            await self.get_viewer_count()
        
        # Check if user is broadcaster
        is_broadcaster = getattr(message.author, "is_broadcaster", False)
        
        # Test command with specific user (broadcaster only)
        if message.content.lower().startswith('!testviewers '):
            if is_broadcaster:
                test_user = message.content.split(' ', 1)[1]
                await self.get_viewer_count(test_user)
            else:
                print(f"Test command attempted by non-broadcaster: {message.author.display_name}")
        
        # Commands to control vignette (broadcaster only)
        if message.content.lower() == '!vignette start':
            if is_broadcaster:
                await self.start_vignette_updates()
            else:
                print(f"Vignette start attempted by non-broadcaster: {message.author.display_name}")
        elif message.content.lower() == '!vignette stop':
            if is_broadcaster:
                await self.stop_vignette_updates()
            else:
                print(f"Vignette stop attempted by non-broadcaster: {message.author.display_name}")
        elif message.content.lower().startswith('!vignette max '):
            if is_broadcaster:
                try:
                    new_max = int(message.content.split(' ', 2)[2])
                    self.max_viewers = new_max
                    print(f"Max viewers set to: {new_max}")
                except ValueError:
                    print("Invalid number for max viewers")
            else:
                print(f"Vignette max attempted by non-broadcaster: {message.author.display_name}")
        
        # Bounce command (broadcaster and mods only)
        elif message.content.lower().startswith('!bounce'):
            is_mod = getattr(message.author, "is_mod", False)
            if is_broadcaster or is_mod:
                # Parse duration parameter
                try:
                    parts = message.content.split()
                    if len(parts) > 1:
                        duration = int(parts[1])
                        # Limit duration to reasonable range (1-300 seconds)
                        duration = max(1, min(300, duration))
                    else:
                        duration = self.bounce_duration  # Use default duration
                    
                    await self.trigger_bounce(duration)
                    print(f"Bounce triggered for {duration} seconds by {message.author.display_name}")
                except ValueError:
                    print(f"Invalid duration parameter. Usage: !bounce [seconds]")
            else:
                print(f"Bounce command attempted by non-broadcaster/moderator: {message.author.display_name}")
    
    async def start_vignette_updates(self):
        """Start the periodic vignette updates."""
        if self.vignette_task is None or self.vignette_task.done():
            self.vignette_task = asyncio.create_task(self._vignette_update_loop())
            print("Vignette updates started")
        else:
            print("Vignette updates already running")
    
    async def stop_vignette_updates(self):
        """Stop the periodic vignette updates."""
        if self.vignette_task and not self.vignette_task.done():
            self.vignette_task.cancel()
            print("Vignette updates stopped")
        else:
            print("Vignette updates not running")
    
    async def _vignette_update_loop(self):
        """Background task to periodically update vignette."""
        while True:
            try:
                await self.update_vignette()
                await asyncio.sleep(self.update_interval)
            except asyncio.CancelledError:
                print("Vignette update loop cancelled")
                break
            except Exception as e:
                print(f"Error in vignette update loop: {e}")
                await asyncio.sleep(self.update_interval)
    
    async def trigger_bounce(self, duration=None):
        """Trigger the bounce effect for specified duration."""
        import time
        if duration is None:
            duration = self.bounce_duration
        
        self.bounce_active = True
        self.bounce_end_time = time.time() + duration
        print(f"Bounce activated for {duration} seconds")
        
        # Send bounce start to overlay
        await self.send_bounce_update(True)
    
    async def update_vignette(self):
        """Get viewer count and update vignette overlay."""
        viewer_count = await self.get_viewer_count('Feer')
        if viewer_count is not None:
            self.current_viewer_count = viewer_count
            
            # Calculate percentage (0% = fully visible, 100% = fully black)
            if viewer_count == 0:
                percentage = 0
            else:
                percentage = min(100, (viewer_count / self.max_viewers) * 100)
            
            print(f"Viewers: {viewer_count}, Vignette: {percentage:.1f}%")
            
            # Check if bounce should end
            import time
            if self.bounce_active and time.time() > self.bounce_end_time:
                self.bounce_active = False
                await self.send_bounce_update(False)
                print("Bounce ended")
            
            # Send update to overlay
            await self.send_vignette_update(percentage)
    
    async def send_vignette_update(self, percentage: float):
        """Send vignette update to overlay."""
        try:
            payload = {
                "type": "vignette",
                "percentage": percentage,
                "viewer_count": self.current_viewer_count,
                "max_viewers": self.max_viewers,
                "bounce_active": self.bounce_active
            }
            
            await self.send_to_overlay(json.dumps(payload))
            print(f"Sent vignette update: {percentage:.1f}%")
            
        except Exception as e:
            print(f"Error sending vignette update: {e}")
    
    async def send_bounce_update(self, bounce_active: bool):
        """Send bounce state update to overlay."""
        try:
            payload = {
                "type": "bounce",
                "active": bounce_active
            }
            
            await self.send_to_overlay(json.dumps(payload))
            print(f"Sent bounce update: {bounce_active}")
            
        except Exception as e:
            print(f"Error sending bounce update: {e}")
    
    async def start_webhook_server(self):
        """Start the webhook server for EventSub."""
        from aiohttp import web
        
        async def handle_eventsub(request):
            """Handle EventSub webhook requests."""
            try:
                # Verify webhook signature
                signature = request.headers.get('Twitch-Eventsub-Message-Signature')
                if not signature:
                    return web.Response(status=403)
                
                # Get the raw body
                body = await request.text()
                
                # Verify the signature (simplified - in production, use proper verification)
                # For now, we'll trust the webhook
                
                # Parse the event data
                data = await request.json()
                
                # Handle different event types
                if data.get('subscription', {}).get('type') == 'channel.subscription.gift':
                    event_data = data.get('event', {})
                    await self.handle_sub_gift(event_data)
                
                return web.Response(status=200)
                
            except Exception as e:
                logger.error(f"Error handling webhook: {e}")
                return web.Response(status=500)
        
        # Create web app
        app = web.Application()
        app.router.add_post('/eventsub', handle_eventsub)
        
        # Start server
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, 'localhost', self.webhook_port)
        await site.start()
        
        logger.info(f"Webhook server started on port {self.webhook_port}")

    async def get_viewer_count(self, test_user: str = None):
        """
        Get the current viewer count for the stream using Twitch Helix API.
        
        Args:
            test_user: Optional username to test with instead of the bot's channel
        
        Returns:
            int: Current viewer count, or None if unable to fetch
        """
        try:
            # Get broadcaster ID (use test user if provided)
            if test_user:
                broadcaster_id = await self.get_user_id(test_user)
                print(f"Testing with user: {test_user}")
            else:
                broadcaster_id = await self.get_broadcaster_id()
            
            if not broadcaster_id:
                print("Could not get broadcaster ID")
                return None
            
            # Get stream information
            url = f"https://api.twitch.tv/helix/streams?user_id={broadcaster_id}"
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Client-Id": self.client_id
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get('data') and len(data['data']) > 0:
                            viewer_count = data['data'][0].get('viewer_count', 0)
                            print(f"Current viewer count: {viewer_count}")
                            return viewer_count
                        else:
                            print("Stream is not currently live")
                            return 0
                    else:
                        error_text = await response.text()
                        print(f"Failed to get stream info. Status: {response.status}, Error: {error_text}")
                        return None
        except Exception as e:
            print(f"Error getting viewer count: {str(e)}")
            return None
    
    async def event_ready(self):
        """Called when the bot is ready and connected to Twitch."""
        logger.info(f'VignetteBot logged in as {self.nick}')
        
        # # Start webhook server for EventSub
        # await self.start_webhook_server()
        
        # Setup EventSub subscriptions
        # await self.setup_eventsub()
        
        # Start token refresh task
        self.token_refresh_task = asyncio.create_task(self._token_refresh_loop())
        
        # Only connect to WebSocket if overlay URL is provided
        if self.overlay_ws_url is not None:
            self.websocket_task = asyncio.create_task(self.connect_websocket())

# Main execution
if __name__ == "__main__":
    # Load environment variables
    load_dotenv()
    
    # Create and run the bot
    bot = VignetteBot()
    bot.run()
