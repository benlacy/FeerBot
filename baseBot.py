import os
import asyncio
import websockets
import logging
from twitchio.ext import commands
from typing import Optional, List

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class BaseBot(commands.Bot):
    def __init__(self, 
                 overlay_ws_url: str,
                 prefix: str = '!',
                 channel_name: str = "Feer",
                 require_client_id: bool = True):
        """
        Initialize the base bot with common configuration.
        
        Args:
            overlay_ws_url: WebSocket URL for the overlay
            prefix: Command prefix for the bot
            channel_name: Twitch channel name to connect to
            require_client_id: Whether to require CLIENT_ID and CLIENT_SECRET
        """
        # Validate environment variables
        self.token = os.getenv("TWITCH_BOT_ACCESS_TOKEN")
        self.client_id = os.getenv("TWITCH_APP_CLIENT_ID") if require_client_id else None
        self.client_secret = os.getenv("TWITCH_APP_CLIENT_SECRET") if require_client_id else None
        self.bot_user_login = "Feer"
        self.channel_name = "Feer"
        
        missing_vars = []
        if not self.token:
            missing_vars.append("TWITCH_BOT_ACCESS_TOKEN")
        if require_client_id and not self.client_id:
            missing_vars.append("TWITCH_APP_CLIENT_ID")
        if require_client_id and not self.client_secret:
            missing_vars.append("TWITCH_APP_CLIENT_SECRET")
            
        if missing_vars:
            logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
            exit(1)

        # Initialize bot with common settings
        super().__init__(
            token=self.token,
            prefix=prefix,
            initial_channels=[channel_name]
        )
        
        # WebSocket settings
        self.overlay_ws_url = overlay_ws_url
        self.ws: Optional[websockets.WebSocketClientProtocol] = None

    async def event_ready(self):
        """Called when the bot is ready and connected to Twitch."""
        logger.info(f'Logged in as {self.nick}')
        await self.connect_websocket()

    async def connect_websocket(self):
        """Maintains a persistent WebSocket connection with exponential backoff."""
        retry_delay = 1
        max_delay = 30
        
        while True:
            try:
                if self.ws is None or self.ws.state != websockets.State.OPEN:
                    self.ws = await websockets.connect(self.overlay_ws_url)
                    logger.info("Connected to WebSocket server")
                    retry_delay = 1  # Reset delay on successful connection
                
                # Prevent tight loop
                await asyncio.sleep(1)
                        
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
                self.ws = None
            except Exception as e:
                logger.error(f"WebSocket connection error: {e}")
                self.ws = None
            
            if self.ws is None:
                # Exponential backoff with max delay
                retry_delay = min(retry_delay * 2, max_delay)
                logger.info(f"Reconnecting in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)

    async def send_to_overlay(self, text: str):
        """Send message using existing WebSocket connection."""
        if self.ws is None or self.ws.state != websockets.State.OPEN:
            logger.warning("WebSocket not connected. Attempting to reconnect...")
            await self.connect_websocket()
            if self.ws is None or self.ws.state != websockets.State.OPEN:
                logger.error("Failed to establish WebSocket connection")
                return
            
        try:
            await self.ws.send(text)
            logger.debug(f"Successfully sent message to overlay: {text}")
        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket closed during send. Reconnecting...")
            self.ws = None
            await self.connect_websocket()
        except Exception as e:
            logger.error(f"Error sending message to overlay: {e}")
            self.ws = None

    async def event_message(self, message):
        """
        Base message handler. Override this in child classes to implement specific message handling.
        """
        if message.echo:
            return
        # Child classes should implement their own message handling logic
        pass 