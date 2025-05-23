import re
from baseBot import BaseBot
import logging
import asyncio
import aiohttp
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class CountingBot(BaseBot):
    # Default timeout duration in seconds (5 minutes)
    TIMEOUT_DURATION = 30

    def __init__(self):
        """
        Initialize the counting bot.
        """
        super().__init__(
            overlay_ws_url="ws://localhost:6790",
            prefix='!',  # We don't actually use commands, but keeping the prefix
            channel_name="Feer",
            require_client_id=True
        )
        self.current_count = 0
        self.record_high = 0  # Track the highest number reached
        self.expected_number = 1  # The next number we're expecting to see
        self.current_streak_users = set()  # Track all users in the current streak
        self.update_lock = asyncio.Lock()  # Lock for thread-safe updates

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

    def is_valid_number(self, message: str) -> bool:
        """
        Check if the message is purely a positive integer.
        Only accepts standalone numbers (e.g., "123").
        Messages containing any other text or characters are not valid.
        
        Args:
            message: The message to check
            
        Returns:
            bool: True if the message is purely a number
        """
        # Check if the entire message is just a number
        if message.strip().isdigit():
            logger.debug(f"Found valid number: {message}")
            return True
        return False

    def extract_number(self, message: str) -> int:
        """
        Extract the number from a message.
        Since we only accept pure numbers now, this simply converts the message to an integer.
        
        Args:
            message: The message to extract from
            
        Returns:
            int: The number if valid, or 0 if invalid
        """
        try:
            return int(message.strip())
        except ValueError:
            return 0

    def timeout_seconds(streak):
        timeout = 7.5 * (2 ** streak)
        return min(timeout, 86400)  # 24-hour cap

    async def timeout_user(self, user_id: str, username: str, duration: int = TIMEOUT_DURATION, reason: str = "Wrong number in counting game"):
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

            # user_id = await self.get_user_id(username)
            # if not user_id:
            #     logger.error(f"Could not get user ID for {username}")
            #     return

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

    async def event_message(self, message):
        """
        Handle incoming messages and update the counter based on the counting game rules.
        Uses a lock to ensure thread safety. If a user who has already participated in the
        current streak tries to count again, it's treated as a failure and resets the counter to 0.
        """
        if message.echo or message.author.display_name == "Nightbot":
            return

        # Get the message content and check if it contains a valid number
        content = message.content.strip()
        username = message.author.display_name
        user_id = message.author.id
            
        if not self.is_valid_number(content):
            return  # Ignore messages that don't contain valid numbers
            
        try:
            number = self.extract_number(content)
            
            # Use lock to ensure thread-safe updates
            async with self.update_lock:
                # Check if this user has already participated in the current streak
                if username in self.current_streak_users:
                    # Treat repeat counting as a failure
                    timeout_duration = self.timeout_seconds(self.current_count)
                    self.current_count = 0
                    self.expected_number = 1
                    self.current_streak_users.clear()  # Clear the streak users
                    logger.info(f"User {username} tried to count again in the same streak - resetting to 0")
                    await self.send_to_overlay(f"COUNT:0:{username}:{self.record_high}:0")
                    # Timeout the user for counting again in the same streak
                    await self.timeout_user(user_id, username, duration=timeout_duration, reason="Counting again in the same streak")
                    return

                # Check if this is the number we're expecting
                if number == self.expected_number:
                    self.current_count = number
                    # Update record high if we've exceeded it
                    is_record = False
                    if number > self.record_high:
                        self.record_high = number
                        is_record = True
                    self.expected_number = number + 1
                    self.current_streak_users.add(username)  # Add user to the streak
                    logger.info(f"Correct number! Count is now {self.current_count} (by {username})")
                    # Send update to overlay with username, record high, and record flag
                    await self.send_to_overlay(f"COUNT:{self.current_count}:{username}:{self.record_high}:{int(is_record)}")
                else:
                    # Wrong number - reset the game and timeout the user
                    right_number = self.expected_number
                    self.current_count = 0
                    self.expected_number = 1
                    self.current_streak_users.clear()  # Clear the streak users
                    logger.info(f"Wrong number ({number})! Resetting to 0 (by {username})")
                    # Send reset to overlay with username, record high, and no record flag
                    await self.send_to_overlay(f"COUNT:0:{username}:{self.record_high}:0")
                    # Timeout the user for getting the wrong number
                    timeout_duration = self.timeout_seconds(right_number-1)
                    await self.timeout_user(user_id, username, duration=timeout_duration, reason=f"Wrong number ({number}). Expected ({right_number})")
                
        except ValueError:
            # This shouldn't happen due to our regex check, but just in case
            logger.error(f"Failed to parse number from message: {content}")

if __name__ == "__main__":
    # Initialize and run the bot
    bot = CountingBot()
    bot.run()