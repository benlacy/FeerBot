from baseBot import BaseBot
import logging
import asyncio
import re
import aiohttp

logger = logging.getLogger(__name__)

class StreakBot(BaseBot):
    def __init__(self):
        """
        Initialize the streak bot to track unique user message streaks.
        """
        super().__init__(
            overlay_ws_url=None,  # No overlay needed
            prefix='!',  # We don't actually use commands, but keeping the prefix
            channel_name="Feer",
            require_client_id=True
        )
        self.valid_messages = ["dsc_1439", "feerDsc1439"]
        self.current_streak = 0
        self.record_streak = 0
        self.participating_users = set()  # Track all users who have participated in current streak
        self.update_lock = asyncio.Lock()  # Lock for thread-safe updates

    def is_valid_message(self, content: str) -> bool:
        """
        Check if a message starts with any of the valid messages.
        
        Args:
            content: The message content to check
            
        Returns:
            bool: True if the message starts with any valid message
        """
        # Check if the message starts with any valid message
        return any(content.startswith(msg) for msg in self.valid_messages)

    def timeout_seconds(self, streak: int) -> int:
        """
        Calculate timeout duration based on streak length.
        
        Args:
            streak: The current streak length
            
        Returns:
            int: Timeout duration in seconds
        """
        timeout = 7.5 * (2 ** streak) + 1
        return min(timeout, 86400)  # 24-hour cap
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
        Handle incoming messages and update the streak based on unique users sending the target message.
        Only new unique users increase the streak. Breaking the streak results in a timeout.
        """
        if message.echo or message.author.display_name == "Nightbot":
            return

        # Get the message content and username
        content = message.content.strip()
        username = message.author.display_name
        user_id = message.author.id
            
        # Use lock to ensure thread-safe updates
        async with self.update_lock:
            # Check if this is a valid message
            if self.is_valid_message(content):
                # Only increment streak if this is a new user
                if username not in self.participating_users:
                    self.current_streak += 1
                    self.participating_users.add(username)
                    # Update record streak if we've exceeded it
                    if self.current_streak > self.record_streak:
                        self.record_streak = self.current_streak
                    logger.info(f"Streak increased to {self.current_streak} by {username}")
            else:
                # Wrong message - reset the streak and timeout the user if streak was high enough
                streak_broken = self.current_streak
                self.current_streak = 0
                self.participating_users.clear()
                
                if streak_broken >= 5:
                    # Calculate timeout and inform chat
                    timeout_duration = self.timeout_seconds(streak_broken)
                    await message.channel.send(f"dsc_1439 {streak_broken} dsc_1439 . Bad @{username}, be gone.")
                    logger.info(f"Streak of {streak_broken} broken by {username}")
                    
                    # Timeout the user for breaking the streak
                    await self.timeout_user(user_id, username, duration=timeout_duration, 
                                         reason=f"Broke the {self.valid_messages[0]} streak of {streak_broken}")
                else:
                    logger.info(f"Streak of {streak_broken} broken by {username} (no timeout - streak too short)")

if __name__ == "__main__":
    # Initialize and run the bot
    bot = StreakBot()
    bot.run() 