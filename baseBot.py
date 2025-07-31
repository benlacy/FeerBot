import os
import asyncio
import websockets
import logging
from twitchio.ext import commands
from typing import Optional, List
from dotenv import load_dotenv
from pathlib import Path
from credential_manager import CredentialManager
import aiohttp

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
        # Initialize credential manager
        self.cred_manager = CredentialManager()
        
        # Validate environment variables
        self.token = self.cred_manager.get_valid_token()
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
        self.token_refresh_task = None

    async def _token_refresh_loop(self):
        """Background task to periodically check and refresh token."""
        while True:
            try:
                # Get a valid token (will refresh if needed)
                token = self.cred_manager.get_valid_token()
                if token and token != self.token:
                    self.token = token
                    # Reconnect the bot with new token
                    await self.close()
                    await self.start()
                await asyncio.sleep(60)  # Check every minute
            except Exception as e:
                logger.error(f"Error in token refresh loop: {e}")
                await asyncio.sleep(60)  # Wait before retrying

    async def event_ready(self):
        """Called when the bot is ready and connected to Twitch."""
        logger.info(f'Logged in as {self.nick}')
        # Start token refresh task now that event loop is running
        self.token_refresh_task = asyncio.create_task(self._token_refresh_loop())
        # Only connect to WebSocket if overlay URL is provided
        if self.overlay_ws_url is not None:
            self.websocket_task = asyncio.create_task(self.connect_websocket())
            await asyncio.sleep(1.5)

    async def connect_websocket(self):
        """Maintains a persistent WebSocket connection with exponential backoff."""
        # Skip connection if no overlay URL is provided
        if self.overlay_ws_url is None:
            logger.info("No overlay WebSocket URL provided, skipping connection")
            return

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
        # Skip if no overlay URL is provided
        if self.overlay_ws_url is None:
            return

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

    async def timeout_user(self, user_id: str, username: str, duration: int = 30, reason: str = "Timeout"):
        """
        Timeout a user in the channel.
        
        Args:
            user_id: The Twitch user ID to timeout
            username: The username of the user (for logging)
            duration: Duration of the timeout in seconds
            reason: Reason for the timeout
        """
        try:
            # Get broadcaster ID from the channel name
            broadcaster_id = await self.get_user_id(self.channel_name)
            if not broadcaster_id:
                logger.error(f"Could not get broadcaster ID for {self.channel_name}")
                return

            # Get bot's user ID for the moderator ID
            moderator_id = await self.get_user_id(self.nick)
            if not moderator_id:
                logger.error(f"Could not get moderator ID for {self.nick}")
                return

            # Prepare the request
            url = f"https://api.twitch.tv/helix/moderation/bans?broadcaster_id={broadcaster_id}&moderator_id={moderator_id}"
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Client-Id": self.client_id,
                "Content-Type": "application/json"
            }
            
            data = {"data":{"user_id": user_id,"duration": duration,"reason": reason}}

            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data) as response:
                    if response.status == 200:
                        logger.info(f"Successfully timed out {username} for {duration} seconds")
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to timeout {username}. Status: {response.status}, Error: {error_text}")

        except Exception as e:
            logger.error(f"Error timing out user {username}: {str(e)}")

    async def get_user_id(self, username: str) -> str:
        """
        Get a Twitch user's ID from their username using the Twitch API.
        
        Args:
            username: The Twitch username to look up
            
        Returns:
            str: The user's ID if found, None otherwise
        """
        try:
            url = f"https://api.twitch.tv/helix/users?login={username}"
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Client-Id": self.client_id
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get('data') and len(data['data']) > 0:
                            return data['data'][0]['id']
                        logger.error(f"No user found with username: {username}")
                        return None
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to get user ID for {username}. Status: {response.status}, Error: {error_text}")
                        return None
                        
        except Exception as e:
            logger.error(f"Error getting user ID for {username}: {str(e)}")
            return None

    async def event_message(self, message):
        """
        Base message handler. Override this in child classes to implement specific message handling.
        """
        if message.echo:
            return
        # Child classes should implement their own message handling logic
        pass 