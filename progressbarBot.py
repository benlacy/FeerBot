import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Tuple
import re
from baseBot import BaseBot

# Configure logging
logger = logging.getLogger(__name__)

# Constants
OVERLAY_WS = "ws://localhost:6790"
INITIAL_SENTIMENT = 50
SENTIMENT_COOLDOWN = 30  # seconds
SENTIMENT_CHANGE = 5
MAX_SENTIMENT = 100
MIN_SENTIMENT = 0

# Sentiment keywords
INCREASE_KEYWORDS = {"ICANT", "ICUMT", "lCUMT", "+2", "WECANT", "Utopia", "LOL"}
DECREASE_KEYWORDS = {"ICAN", "WECAN", "-2"}

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

class ProgressBarBot(BaseBot):
    def __init__(self):
        super().__init__(
            overlay_ws_url=OVERLAY_WS,
            prefix='!',
            channel_name="Feer"
        )
        self.sentiment_tracker = SentimentTracker()

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

if __name__ == '__main__':
    bot = ProgressBarBot()
    bot.run()
