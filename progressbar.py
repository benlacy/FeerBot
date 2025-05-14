import os
import asyncio
import websockets
import logging
from typing import Dict, Tuple, Optional
from twitchio.ext import commands
from datetime import datetime, timezone, timedelta
import re

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
OVERLAY_WS = "ws://localhost:6790"
PREFIX = '!'
INITIAL_SENTIMENT = 50
SENTIMENT_COOLDOWN = 30  # seconds
SENTIMENT_CHANGE = 5
MAX_SENTIMENT = 100
MIN_SENTIMENT = 0

# Sentiment keywords
INCREASE_KEYWORDS = {"ICANT", "ICUMT", "lCUMT", "+2", "WECANT", "Utopia", "LOL"}
DECREASE_KEYWORDS = {"ICAN", "WECAN", "-2"}

# Environment variables
TOKEN = os.getenv("TWITCH_BOT_ACCESS_TOKEN")
CLIENT_ID = os.getenv("TWITCH_APP_CLIENT_ID")
CLIENT_SECRET = os.getenv("TWITCH_APP_CLIENT_SECRET")
CHANNEL_NAME = "Feer"

# Validate environment variables
missing_vars = [var for var, value in {
    "TWITCH_BOT_ACCESS_TOKEN": TOKEN,
    "TWITCH_APP_CLIENT_ID": CLIENT_ID,
    "TWITCH_APP_CLIENT_SECRET": CLIENT_SECRET
}.items() if not value]

if missing_vars:
    logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
    exit(1)

CHANNELS = [CHANNEL_NAME]

class SentimentTracker:
    def __init__(self, initial_value: int = INITIAL_SENTIMENT):
        self.total_sentiment = initial_value
        self.user_sentiments: Dict[str, Tuple[int, datetime]] = {}

    def update_sentiment(self, username: str, value: int) -> bool:
        """Update sentiment for a user and return whether the update was applied."""
        current_time = datetime.now(timezone.utc)
        
        if username not in self.user_sentiments:
            self.user_sentiments[username] = (value, current_time)
            return True
            
        prev_value, prev_time = self.user_sentiments[username]
        time_diff = current_time - prev_time
        
        if value != prev_value or time_diff > timedelta(seconds=SENTIMENT_COOLDOWN):
            self.user_sentiments[username] = (value, current_time)
            return True
            
        return False

    def adjust_total(self, value: int) -> int:
        """Adjust total sentiment and return the new value."""
        self.total_sentiment = max(MIN_SENTIMENT, min(MAX_SENTIMENT, self.total_sentiment + value))
        return self.total_sentiment

class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            token=TOKEN,
            prefix=PREFIX,
            initial_channels=CHANNELS
        )
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.sentiment_tracker = SentimentTracker()

    async def event_ready(self):
        logger.info(f'Logged in as {self.nick}')
        await self.connect_websocket()

    def get_progress_bar_change(self, user_input: str) -> int:
        """Determine sentiment change from user input."""
        cleaned_input = re.sub(r'\s+', '', user_input)
        
        if any(keyword in cleaned_input for keyword in INCREASE_KEYWORDS):
            return SENTIMENT_CHANGE
        if any(keyword in cleaned_input for keyword in DECREASE_KEYWORDS):
            return -SENTIMENT_CHANGE
        return 0

    async def event_message(self, message):
        if message.echo:
            return
        
        value = self.get_progress_bar_change(message.content)
        if value == 0:
            logger.debug(f'No sentiment change for message: {message.content}')
            return

        # Only send to overlay if the sentiment actually changes
        if self.sentiment_tracker.update_sentiment(message.author.display_name, value):
            new_sentiment = self.sentiment_tracker.adjust_total(value)
            logger.info(f'Sentiment updated to: {new_sentiment} (change: {value:+d}) (by {message.author.display_name})')
            await self.send_to_overlay(str(new_sentiment))
        else:
            logger.debug(f'Sentiment update ignored due to cooldown for user: {message.author.display_name}')

    async def connect_websocket(self):
        """Maintains a persistent WebSocket connection with exponential backoff."""
        retry_delay = 1
        max_delay = 30
        
        while True:
            try:
                async with websockets.connect(OVERLAY_WS) as ws:
                    self.ws = ws
                    logger.info("Connected to WebSocket overlay")
                    retry_delay = 1  # Reset delay on successful connection
                    
                    while True:
                        await asyncio.sleep(1)
                        
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
            except Exception as e:
                logger.error(f"WebSocket connection error: {e}")
            
            # Exponential backoff with max delay
            retry_delay = min(retry_delay * 2, max_delay)
            logger.info(f"Reconnecting in {retry_delay} seconds...")
            await asyncio.sleep(retry_delay)

    async def send_to_overlay(self, text: str):
        """Send message using existing WebSocket connection."""
        if not self.ws or self.ws.state != websockets.State.OPEN:
            logger.warning("WebSocket not connected. Attempting to reconnect...")
            await self.connect_websocket()
            return
            
        try:
            await self.ws.send(text)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket closed during send. Reconnecting...")
            await self.connect_websocket()

if __name__ == '__main__':
    bot = Bot()
    bot.run()
