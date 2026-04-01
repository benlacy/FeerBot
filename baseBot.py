import os
import ssl
import asyncio
import websockets
import logging
from twitchio.ext import commands
from typing import Optional, List
from dotenv import load_dotenv
from pathlib import Path
from credential_manager import CredentialManager
import aiohttp
import json
from datetime import datetime, timezone
import tempfile
import requests
from gtts import gTTS
import time
import re

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
        self.moderator_id = None
        self.broadcaster_id = None
        
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
        
        # TTS configuration
        self.tts_api_url = "https://api.console.tts.monster/generate"
        self.tts_api_token = os.getenv("TTS_MONSTER_API_TOKEN")
        self.default_voice_id = "67dbd94d-a097-4676-af2f-1db67c1eb8dd"  # Default voice ID
        
        # YouTube API configuration
        self.youtube_api_key = os.getenv("YOUTUBE_API_KEY")
        
        # Browser for viewer count scraping (lazy initialization)
        self._viewer_count_driver = None
        self._viewer_count_url = None
        self._youtube_viewer_count_url = None
        self._last_scraped_domain = None  # Track which domain we last scraped

    async def _token_refresh_loop(self):
        """Background task to periodically check and refresh token."""
        while True:
            try:
                # Get a valid token (will refresh if needed)
                token = self.cred_manager.get_valid_token()
                if token and token != self.token:
                    logger.info("Token refreshed, reconnecting bot...")
                    self.token = token

                    # Reconnect the bot with new token
                    # await self.close()
                    await self.connect()
                    
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

        #Test code to check banned users
        # await self.get_banned_users()
        # uid = await self.get_user_id("eros__rl")
        # await self.untimeout_user(uid, "eros__rl")

    async def get_broadcaster_id(self):
        if self.broadcaster_id is None:
            self.broadcaster_id = await self.get_user_id(self.channel_name)
            if not self.broadcaster_id:
                logger.error(f"Could not get broadcaster ID for {self.channel_name}")
                return
        return self.broadcaster_id

    async def get_banned_users(self):
        """
        Get a list of banned users from the channel.
        
        Args:
            None
            
        Returns:
            list: A list of banned users
        """

        # Get broadcaster ID from the channel name
        self.broadcaster_id = await self.get_broadcaster_id()

        try:
            url = f"https://api.twitch.tv/helix/moderation/banned?broadcaster_id={self.broadcaster_id}&first=100"
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Client-Id": self.client_id
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        payload = await response.json()
                        items = payload.get('data', [])
                        return items
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to fetch banned list. Status: {response.status}, Error: {error_text}")
                        return []
        except Exception as e:
            logger.error(f"error getting banned users: {str(e)}")
            return []

    def _parse_iso8601_utc(self, value: str) -> Optional[datetime]:
        if not value:
            return None
        try:
            # Values look like '2025-09-12T00:18:17Z'
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except Exception:
            try:
                # Fallback: allow fromisoformat with Z -> +00:00
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except Exception:
                return None

    @commands.command(name='timeouts')
    async def timeouts_command(self, ctx: commands.Context):
        """Log users currently timed out, soonest to expire first. (Mods/Broadcaster only)"""
        is_broadcaster = getattr(ctx.author, "is_broadcaster", False)
        is_mod = getattr(ctx.author, "is_mod", False)
        if not (is_broadcaster or is_mod):
            return

        # Ensure broadcaster id is available
        self.broadcaster_id = await self.get_broadcaster_id()
        items = await self.get_banned_users()

        # Filter to timeouts (non-empty expires_at) and sort by soonest expiration
        timeouts = [it for it in items if it.get('expires_at')]
        if not timeouts:
            logger.info("No active timeouts.")
            return

        def sort_key(it):
            dt = self._parse_iso8601_utc(it.get('expires_at', ''))
            # None goes to far future
            return dt or datetime.max.replace(tzinfo=timezone.utc)

        timeouts.sort(key=sort_key)

        # Log line-by-line
        for it in timeouts:
            user = it.get('user_login') or it.get('user_name') or it.get('user_id')
            expires = self._parse_iso8601_utc(it.get('expires_at'))
            mod = it.get('moderator_login') or it.get('moderator_name')
            if not expires:
                logger.info(f"timeout: {user} remaining unknown (by {mod})")
                continue
            now_utc = datetime.now(timezone.utc)
            remaining = expires - now_utc
            if remaining.total_seconds() < 0:
                remaining = remaining.__class__(0)
            days = remaining.days
            hours = (remaining.seconds // 3600)
            minutes = (remaining.seconds % 3600) // 60
            logger.info(f"timeout: {user} {days}d {hours}h {minutes}m remaining (by {mod})")

        # Send structured payload to overlay (no chat output)
        payload_items = []
        for it in timeouts:
            user = it.get('user_login') or it.get('user_name') or it.get('user_id')
            expires = self._parse_iso8601_utc(it.get('expires_at'))
            if not expires:
                remaining_str = "unknown"
            else:
                now_utc = datetime.now(timezone.utc)
                remaining = expires - now_utc
                if remaining.total_seconds() < 0:
                    remaining = remaining.__class__(0)
                days = remaining.days
                hours = (remaining.seconds // 3600)
                minutes = (remaining.seconds % 3600) // 60
                remaining_str = f"{days}d {hours}h {minutes}m"
            payload_items.append({
                "user": user,
                "remaining": remaining_str,
            })

        try:
            await self.send_to_overlay(json.dumps({
                "type": "timeouts",
                "items": payload_items
            }))
        except Exception as e:
            logger.error(f"Failed sending timeouts to overlay: {str(e)}")

        # await self.ws.close()
        # await self.close()
        # await self.start()
        # await self.connect()

    async def connect_websocket(self):
        """Maintains a persistent WebSocket connection with exponential backoff."""
        # Skip connection if no overlay URL is provided
        if self.overlay_ws_url is None:
            logger.info("No overlay WebSocket URL provided, skipping connection")
            return

        # retry_delay = 1
        # max_delay = 30
        
        # while True:
        #     try:
        #         if self.ws is None or self.ws.state != websockets.State.OPEN:
        #             self.ws = await websockets.connect(self.overlay_ws_url)
        #             logger.info("Connected to WebSocket server")
        #             retry_delay = 1  # Reset delay on successful connection
                
        #         # Prevent tight loop
        #         await asyncio.sleep(1)
                        
        #     except websockets.exceptions.ConnectionClosed as e:
        #         logger.warning(f"WebSocket connection closed: {e}")
        #         self.ws = None
        #     except Exception as e:
        #         logger.error(f"WebSocket connection error: {e}")
        #         self.ws = None
            
        #     if self.ws is None:
        #         # Exponential backoff with max delay
        #         retry_delay = min(retry_delay * 2, max_delay)
        #         logger.info(f"Reconnecting in {retry_delay} seconds...")
        #         await asyncio.sleep(retry_delay)
        def _overlay_connect_kwargs() -> dict:
            """Extra kwargs for wss:// (optional verify skip for self-signed dev only)."""
            if not self.overlay_ws_url.startswith("wss://"):
                return {}
            verify = os.getenv("OVERLAY_WS_SSL_VERIFY", "1").lower() not in (
                "0", "false", "no",
            )
            if verify:
                return {}
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            logger.warning(
                "OVERLAY_WS_SSL_VERIFY disabled — only for dev/self-signed certs"
            )
            return {"ssl": ctx}

        while True:
            logger.info("Attempting to connect to WebSocket server...")
            try:
                async with websockets.connect(
                    self.overlay_ws_url,
                    **_overlay_connect_kwargs(),
                ) as websocket:
                    logger.info("Connected to WebSocket server")
                    self.ws = websocket
                    await self.upon_connection()
                    # Keep connection alive and handle messages
                    try:
                        async for message in websocket:
                            try:
                                await self.handle_websocket_message(message)
                            except Exception as e:
                                logger.error(f"Error handling websocket message: {e}")
                    except websockets.exceptions.ConnectionClosed:
                        pass
                    
                    self.ws = None
                    logger.info("WebSocket connection closed")
            except websockets.exceptions.ConnectionClosed:
                self.ws = None
                logger.info("Connection closed by server")
            except Exception as e:
                self.ws = None
                logger.error(f"Error: {e}")
                
            await asyncio.sleep(2)

    async def handle_websocket_message(self, message: str):
        """
        Handle incoming messages from the overlay WebSocket.
        Child classes can override this to handle specific messages.
        
        Args:
            message: The raw message string from the websocket
        """
        try:
            data = json.loads(message)
            if isinstance(data, dict) and data.get("action"):
                # Let child classes handle specific actions
                await self.on_websocket_action(data.get("action"), data)
        except json.JSONDecodeError:
            # Not JSON, ignore
            pass
        except Exception as e:
            logger.debug(f"Error processing websocket message: {e}")

    async def on_websocket_action(self, action: str, data: dict):
        """
        Handle specific websocket actions. Override in child classes.
        
        Args:
            action: The action name (e.g., "get_king")
            data: The full message data
        """
        pass

    async def send_to_overlay(self, text: str):
        """Send message using existing WebSocket connection."""
        # Skip if no overlay URL is provided
        if self.overlay_ws_url is None:
            return

        if self.ws is None or self.ws.state != websockets.State.OPEN:
            logger.warning("WebSocket not connected. Message not sent.")
            return
            
        try:
            await self.ws.send(text)
            logger.debug(f"Successfully sent message to overlay: {text}")
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
            self.broadcaster_id = await self.get_broadcaster_id()

            # Get bot's user ID for the moderator ID
            if self.moderator_id is None:
                self.moderator_id = await self.get_user_id(self.nick)
                if not self.moderator_id:
                    logger.error(f"Could not get moderator ID for {self.nick}")
                    return

            # Prepare the request
            url = f"https://api.twitch.tv/helix/moderation/bans?broadcaster_id={self.broadcaster_id}&moderator_id={self.moderator_id}"
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

    async def untimeout_user(self, user_id: str, username: str) -> None:
        """
        Remove an active timeout/ban for a user in the channel.

        Args:
            user_id: The Twitch user ID to untimeout/unban
            username: The username of the user (for logging)
        """
        try:
            self.broadcaster_id = await self.get_broadcaster_id()

            if self.moderator_id is None:
                self.moderator_id = await self.get_user_id(self.nick)
                if not self.moderator_id:
                    logger.error(f"Could not get moderator ID for {self.nick}")
                    return

            # DELETE removes the ban/timeout
            url = (
                "https://api.twitch.tv/helix/moderation/bans"
                f"?broadcaster_id={self.broadcaster_id}&moderator_id={self.moderator_id}&user_id={user_id}"
            )
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Client-Id": self.client_id,
            }

            async with aiohttp.ClientSession() as session:
                async with session.delete(url, headers=headers) as response:
                    if response.status in (200, 204):
                        logger.info(f"Successfully removed timeout/ban for {username}")
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"Failed to remove timeout/ban for {username}. Status: {response.status}, Error: {error_text}"
                        )
        except Exception as e:
            logger.error(f"Error removing timeout/ban for {username}: {str(e)}")

    async def add_vip(self, user_id: str, username: str) -> bool:
        """
        Add VIP status to a user in the channel.
        
        Args:
            user_id: The Twitch user ID to grant VIP status to
            username: The username of the user (for logging)
            
        Returns:
            bool: True if successful, False otherwise
            
        Note:
            Requires channel:manage:vips scope and broadcaster authentication.
            Rate limit: 10 VIP operations per 10 seconds.
        """
        try:
            self.broadcaster_id = await self.get_broadcaster_id()
            if not self.broadcaster_id:
                logger.error("Could not get broadcaster ID")
                return False

            url = f"https://api.twitch.tv/helix/channels/vips?broadcaster_id={self.broadcaster_id}&user_id={user_id}"
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Client-Id": self.client_id
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers) as response:
                    if response.status == 204:
                        logger.info(f"Successfully added VIP status to {username}")
                        return True
                    elif response.status == 200:
                        logger.info(f"Successfully added VIP status to {username}")
                        return True
                    else:
                        error_text = await response.text()
                        if response.status == 425:
                            logger.error(f"Failed to add VIP to {username}: Broadcaster must complete 'Build a Community' requirements")
                        elif response.status == 429:
                            logger.error(f"Failed to add VIP to {username}: Rate limit exceeded (max 10 VIP operations per 10 seconds)")
                        else:
                            logger.error(f"Failed to add VIP to {username}. Status: {response.status}, Error: {error_text}")
                        return False

        except Exception as e:
            logger.error(f"Error adding VIP to {username}: {str(e)}")
            return False

    async def remove_vip(self, user_id: str, username: str) -> bool:
        """
        Remove VIP status from a user in the channel.
        
        Args:
            user_id: The Twitch user ID to remove VIP status from
            username: The username of the user (for logging)
            
        Returns:
            bool: True if successful, False otherwise
            
        Note:
            Requires channel:manage:vips scope and broadcaster authentication.
            Rate limit: 10 VIP operations per 10 seconds.
        """
        try:
            self.broadcaster_id = await self.get_broadcaster_id()
            if not self.broadcaster_id:
                logger.error("Could not get broadcaster ID")
                return False

            url = f"https://api.twitch.tv/helix/channels/vips?broadcaster_id={self.broadcaster_id}&user_id={user_id}"
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Client-Id": self.client_id
            }

            async with aiohttp.ClientSession() as session:
                async with session.delete(url, headers=headers) as response:
                    if response.status == 204:
                        logger.info(f"Successfully removed VIP status from {username}")
                        return True
                    elif response.status == 200:
                        logger.info(f"Successfully removed VIP status from {username}")
                        return True
                    else:
                        error_text = await response.text()
                        if response.status == 429:
                            logger.error(f"Failed to remove VIP from {username}: Rate limit exceeded (max 10 VIP operations per 10 seconds)")
                        else:
                            logger.error(f"Failed to remove VIP from {username}. Status: {response.status}, Error: {error_text}")
                        return False

        except Exception as e:
            logger.error(f"Error removing VIP from {username}: {str(e)}")
            return False

    async def is_vip(self, user_id: str) -> bool:
        """
        Check if a user has VIP status in the channel.
        
        Args:
            user_id: The Twitch user ID to check
            
        Returns:
            bool: True if user has VIP status, False otherwise
        """
        try:
            self.broadcaster_id = await self.get_broadcaster_id()
            if not self.broadcaster_id:
                logger.error("Could not get broadcaster ID")
                return False

            url = f"https://api.twitch.tv/helix/channels/vips?broadcaster_id={self.broadcaster_id}&user_id={user_id}"
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Client-Id": self.client_id
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        # If user is VIP, the API returns their data
                        return len(data.get('data', [])) > 0
                    elif response.status == 404:
                        # User is not a VIP
                        return False
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to check VIP status for user {user_id}. Status: {response.status}, Error: {error_text}")
                        return False

        except Exception as e:
            logger.error(f"Error checking VIP status for user {user_id}: {str(e)}")
            return False

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

    def _init_viewer_count_browser(self):
        """Lazy initialization of browser for viewer count scraping."""
        if self._viewer_count_driver is None:
            try:
                from selenium import webdriver
                from selenium.webdriver.firefox.options import Options
                
                options = Options()
                options.add_argument("--headless")
                options.set_preference("dom.webdriver.enabled", False)
                options.set_preference("useAutomationExtension", False)
                options.set_preference("general.useragent.override", 
                                     "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0")
                
                self._viewer_count_driver = webdriver.Firefox(options=options)
                self._viewer_count_driver.set_page_load_timeout(30)
                logger.info("Firefox browser initialized for viewer count")
            except Exception as e:
                logger.error(f"Failed to initialize browser for viewer count: {e}")
                self._viewer_count_driver = None
    
    async def get_viewer_count(self, stream_name: str = None):
        """
        Get current viewer count by scraping Twitch page.
        
        Args:
            stream_name: Stream name to check (defaults to channel_name)
        
        Returns:
            int: Viewer count, or None if error
        """
        if stream_name is None:
            stream_name = self.channel_name
        
        self._init_viewer_count_browser()
        if not self._viewer_count_driver:
            return None
        
        url = f"https://www.twitch.tv/{stream_name}"
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._scrape_viewer_count, url)
        except Exception as e:
            logger.error(f"Error getting viewer count: {e}")
            return None
    
    def _scrape_viewer_count(self, url: str):
        """Scrape viewer count from Twitch page (synchronous)."""
        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.common.exceptions import TimeoutException, NoSuchElementException
            
            # Check if we're switching domains (e.g., from YouTube to Twitch)
            current_domain = "twitch.tv"
            if self._last_scraped_domain and self._last_scraped_domain != current_domain:
                # Switching domains - always reload
                self._viewer_count_driver.get(url)
                self._viewer_count_url = url
                self._last_scraped_domain = current_domain
                time.sleep(5)  # Wait for JavaScript to load
            elif self._viewer_count_url != url:
                # Same domain, different URL - reload
                self._viewer_count_driver.get(url)
                self._viewer_count_url = url
                self._last_scraped_domain = current_domain
                time.sleep(5)  # Wait for JavaScript to load
            else:
                # Same URL - just wait for updates
                time.sleep(2)  # Brief wait for updates
            
            wait = WebDriverWait(self._viewer_count_driver, 10)
            selector = '[data-a-target="animated-channel-viewers-count"]'
            
            try:
                element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                text = element.text.strip()
                if text:
                    count = self._parse_viewer_count(text)
                    if count is not None:
                        return count
                    else:
                        logger.debug(f"Could not parse viewer count from text: '{text}'")
                else:
                    logger.debug("Viewer count element found but text is empty")
                
                # Try alternative selectors if primary fails
                alternative_selectors = [
                    '[data-a-target="animated-channel-viewers-count"]',
                    'p[data-test-selector="animated-channel-viewers-count"]',
                    'span[data-a-target="animated-channel-viewers-count"]',
                ]
                
                for alt_selector in alternative_selectors:
                    try:
                        alt_element = self._viewer_count_driver.find_element(By.CSS_SELECTOR, alt_selector)
                        alt_text = alt_element.text.strip()
                        if alt_text:
                            count = self._parse_viewer_count(alt_text)
                            if count is not None:
                                logger.debug(f"Found viewer count using alternative selector: {alt_selector}")
                                return count
                    except NoSuchElementException:
                        continue
                
                logger.debug("Could not find viewer count element - stream may be offline")
                return 0  # Stream offline or element not found
            except (TimeoutException, NoSuchElementException):
                logger.debug(f"Viewer count element not found - stream may be offline or selector changed")
                return 0  # Stream offline or element not found
                
        except Exception as e:
            logger.error(f"Error scraping viewer count: {e}")
            return None
    
    def _parse_viewer_count(self, text: str):
        """Parse viewer count from text (handles K, M suffixes)."""
        if not text:
            return None
        
        text = text.replace(',', '').replace(' ', '').upper()
        match = re.search(r'([\d.]+)([KM]?)', text)
        
        if match:
            number = float(match.group(1))
            suffix = match.group(2)
            
            if suffix == 'K':
                return int(number * 1000)
            elif suffix == 'M':
                return int(number * 1000000)
            else:
                return int(number)
        
        return None

    async def get_youtube_viewer_count(self, channel_id: str = "Feer", video_id: str = None, use_scraping: bool = True):
        """
        Get current viewer count from YouTube live stream.
        Uses web scraping by default to avoid API quota limits.
        
        Args:
            channel_id: YouTube channel handle (e.g., "Feer" for @Feer) or channel ID
            video_id: Optional specific video/stream ID to check directly
            use_scraping: If True, use web scraping. If False, try API first (default: True)
        
        Returns:
            int: Concurrent viewer count, or None if error/not live
        """
        # Use web scraping by default to avoid quota issues
        if use_scraping:
            return await self._get_youtube_viewer_count_scraping(channel_id, video_id)
        
        # Fallback to API if scraping is disabled and API key is available
        if not self.youtube_api_key:
            logger.warning("YOUTUBE_API_KEY not set and scraping disabled. Cannot get YouTube viewer count.")
            return None
        
        try:
            # If video_id is provided, check that specific video directly
            if video_id:
                url = f"https://www.googleapis.com/youtube/v3/videos"
                params = {
                    "part": "liveStreamingDetails",
                    "id": video_id,
                    "key": self.youtube_api_key
                }
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, params=params) as response:
                        if response.status == 200:
                            data = await response.json()
                            items = data.get("items", [])
                            if items:
                                live_details = items[0].get("liveStreamingDetails", {})
                                concurrent_viewers = live_details.get("concurrentViewers")
                                if concurrent_viewers:
                                    return int(concurrent_viewers)
                                else:
                                    logger.debug(f"YouTube video {video_id} is not live or has no concurrent viewers")
                                    return None
                            else:
                                logger.debug(f"YouTube video {video_id} not found")
                                return None
                        else:
                            error_text = await response.text()
                            logger.error(f"YouTube API error: Status {response.status}, Error: {error_text}")
                            # Fallback to scraping if API fails
                            return await self._get_youtube_viewer_count_scraping(channel_id, video_id)
            
            # If only channel_id is provided, search for active live streams
            elif channel_id:
                # First, search for active live broadcasts
                search_url = f"https://www.googleapis.com/youtube/v3/search"
                search_params = {
                    "part": "snippet",
                    "channelId": channel_id,
                    "eventType": "live",
                    "type": "video",
                    "key": self.youtube_api_key
                }
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(search_url, params=search_params) as response:
                        if response.status == 200:
                            data = await response.json()
                            items = data.get("items", [])
                            
                            if not items:
                                logger.debug(f"No active live streams found for channel {channel_id}")
                                return None
                            
                            # Get the first live video ID
                            live_video_id = items[0]["id"]["videoId"]
                            
                            # Now get the live stream details
                            video_url = f"https://www.googleapis.com/youtube/v3/videos"
                            video_params = {
                                "part": "liveStreamingDetails",
                                "id": live_video_id,
                                "key": self.youtube_api_key
                            }
                            
                            async with session.get(video_url, params=video_params) as video_response:
                                if video_response.status == 200:
                                    video_data = await video_response.json()
                                    video_items = video_data.get("items", [])
                                    if video_items:
                                        live_details = video_items[0].get("liveStreamingDetails", {})
                                        concurrent_viewers = live_details.get("concurrentViewers")
                                        if concurrent_viewers:
                                            return int(concurrent_viewers)
                                        else:
                                            logger.debug(f"YouTube stream {live_video_id} has no concurrent viewers")
                                            return None
                                    else:
                                        logger.debug(f"YouTube video {live_video_id} not found")
                                        return None
                                else:
                                    error_text = await video_response.text()
                                    logger.error(f"YouTube API error getting video details: Status {video_response.status}, Error: {error_text}")
                                    # Fallback to scraping if API fails
                                    return await self._get_youtube_viewer_count_scraping(channel_id, None)
                        else:
                            error_text = await response.text()
                            logger.error(f"YouTube API error searching for live streams: Status {response.status}, Error: {error_text}")
                            # Fallback to scraping if API fails
                            return await self._get_youtube_viewer_count_scraping(channel_id, None)
            else:
                logger.error("Either channel_id or video_id must be provided to get_youtube_viewer_count")
                return None
                
        except Exception as e:
            logger.error(f"Error getting YouTube viewer count: {e}")
            # Fallback to scraping if API fails
            return await self._get_youtube_viewer_count_scraping(channel_id, video_id)
    
    async def _get_youtube_viewer_count_scraping(self, channel_id: str = None, video_id: str = None):
        """
        Get YouTube viewer count by scraping the live stream page.
        
        Args:
            channel_id: YouTube channel handle (e.g., "Feer" for @Feer) or channel ID
            video_id: Optional specific video/stream ID to check directly
        
        Returns:
            int: Concurrent viewer count, or None if error/not live
        """
        self._init_viewer_count_browser()
        if not self._viewer_count_driver:
            return None
        
        # Build URL - prefer video_id if provided, otherwise use channel
        if video_id:
            url = f"https://www.youtube.com/watch?v={video_id}"
        elif channel_id:
            # Try handle format first (@channel), fallback to channel ID format
            if channel_id.startswith("UC") and len(channel_id) > 20:
                url = f"https://www.youtube.com/channel/{channel_id}/live"
            else:
                # Remove @ if present
                handle = channel_id.lstrip('@')
                url = f"https://www.youtube.com/@{handle}/live"
        else:
            logger.error("Either channel_id or video_id must be provided")
            return None
        
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._scrape_youtube_viewer_count, url)
        except Exception as e:
            logger.error(f"Error getting YouTube viewer count via scraping: {e}")
            return None
    
    def _scrape_youtube_viewer_count(self, url: str):
        """Scrape viewer count from YouTube live stream page (synchronous)."""
        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.common.exceptions import TimeoutException, NoSuchElementException
            
            # Check if we're switching domains (e.g., from Twitch to YouTube)
            current_domain = "youtube.com"
            if self._last_scraped_domain and self._last_scraped_domain != current_domain:
                # Switching domains - always reload
                self._viewer_count_driver.get(url)
                self._youtube_viewer_count_url = url
                self._last_scraped_domain = current_domain
                time.sleep(5)  # Wait for JavaScript to load
            elif self._youtube_viewer_count_url != url:
                # Same domain, different URL - reload
                self._viewer_count_driver.get(url)
                self._youtube_viewer_count_url = url
                self._last_scraped_domain = current_domain
                time.sleep(5)  # Wait for JavaScript to load
            else:
                # Same URL - just wait for updates
                time.sleep(2)  # Brief wait for updates
            
            wait = WebDriverWait(self._viewer_count_driver, 10)
            
            # Try multiple selectors for YouTube viewer count
            # YouTube's viewer count can appear in different places
            selectors = [
                'span[class*="view-count"]',  # Common viewer count selector
                'div[class*="view-count"]',
                'span[aria-label*="watching"]',  # "X watching" text
                'div[id="info"] span',  # Info section
                'ytd-live-chat-header-renderer span',  # Live chat header
            ]
            
            for selector in selectors:
                try:
                    elements = self._viewer_count_driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        text = element.text.strip()
                        # Look for numbers that might be viewer count
                        # YouTube shows "X watching" or just the number
                        if text and ('watching' in text.lower() or text.replace(',', '').replace(' ', '').isdigit()):
                            # Extract number from text
                            numbers = re.findall(r'[\d,]+', text)
                            if numbers:
                                # Take the first number found
                                count_text = numbers[0].replace(',', '')
                                count = int(count_text)
                                if count > 0:  # Valid viewer count
                                    return count
                except (TimeoutException, NoSuchElementException):
                    continue
                except Exception as e:
                    logger.debug(f"Error with selector {selector}: {e}")
                    continue
            
            # Alternative: Try to find viewer count in page source
            try:
                page_source = self._viewer_count_driver.page_source
                # Look for patterns like "X watching" or viewer count in metadata
                watching_pattern = r'(\d+(?:,\d+)*)\s*watching'
                match = re.search(watching_pattern, page_source, re.IGNORECASE)
                if match:
                    count_text = match.group(1).replace(',', '')
                    return int(count_text)
            except Exception as e:
                logger.debug(f"Error searching page source: {e}")
            
            return 0  # Stream offline or element not found
                
        except Exception as e:
            logger.error(f"Error scraping YouTube viewer count: {e}")
            return None

    async def upon_connection(self):
        # Child classes should implement
        pass

    def _playsound_file(self, path: str) -> None:
        """Lazy import so bots that never play audio (e.g. ViewTestBot on a server) skip playsound."""
        try:
            import playsound as _playsound
        except ImportError as e:
            raise ImportError(
                "playsound is required for speak/speakMonster. pip install playsound"
            ) from e
        _playsound.playsound(path)

    async def speak(self, message: str, voice_id: Optional[str] = None):
        """
        Generate TTS using gTTS and play the audio.
        
        Args:
            message: The text to convert to speech
            voice_id: Optional voice ID (not used for gTTS, kept for compatibility)
        """
        try:
            tts = gTTS(message, lang='en', tld='us')
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
                temp_path = fp.name
                tts.save(temp_path)
            try:
                self._playsound_file(temp_path)
            finally:
                os.unlink(temp_path)
        except Exception as e:
            logger.error(f"Error in speak: {e}")

    async def speakMonster(self, message: str, voice_id: Optional[str] = None):
        """
        Generate TTS using TTSMonster API and play the audio.
        
        Args:
            message: The text to convert to speech
            voice_id: Optional voice ID, uses default if not provided
        """
        try:
            # Use provided voice_id or default
            selected_voice_id = voice_id or self.default_voice_id
            
            # Prepare the request payload
            payload = {
                "voice_id": selected_voice_id,
                "message": message[:500]  # Limit to 500 characters as per API docs
            }
            
            # Set up headers
            headers = {
                "Content-Type": "application/json",
                "Authorization": self.tts_api_token
            }
            
            # Make the API request
            response = requests.post(
                self.tts_api_url,
                headers=headers,
                data=json.dumps(payload)
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get("status") == 200:
                    audio_url = result.get("url")
                    
                    # Download the audio file
                    audio_response = requests.get(audio_url)
                    if audio_response.status_code == 200:
                        # Save to temporary file
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as fp:
                            temp_path = fp.name
                            fp.write(audio_response.content)
                        
                        # Play the audio
                        try:
                            self._playsound_file(temp_path)
                        finally:
                            os.unlink(temp_path)
                    else:
                        logger.error(f"Failed to download audio: {audio_response.status_code}")
                else:
                    logger.error(f"TTS API error: {result}")
            else:
                logger.error(f"TTS API request failed: {response.status_code} - {response.text}")
                
        except Exception as e:
            logger.error(f"Error in speakMonster: {e}")

    async def event_message(self, message):
        """
        Base message handler. Override this in child classes to implement specific message handling.
        """
        if message.echo:
            return
        # Child classes should implement their own message handling logic
        pass 