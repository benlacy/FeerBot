import asyncio
import logging
import json
import random
import re
from datetime import datetime, timezone
from collections import deque
from twitchio.ext import commands
from baseBot import BaseBot

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

OVERLAY_WS = "ws://localhost:6790"
TIMER_DURATION = 60  # Countdown timer duration (always 1 minute)
INITIAL_BAN_DURATION = 180  # Starting ban duration in seconds (3 minutes)
MAX_BAN_DURATION = 1209600  # Maximum ban duration (2 weeks in seconds)
BAN_MULTIPLIER = 1.258  # Exponential multiplier for ban duration (reaches ~2 weeks after 50 passes)
INITIAL_SELECTION_MESSAGE_COUNT = 5  # Last 5 messages for initial selection
PASS_MESSAGE_COUNT = 200  # Last 200 messages for passing eligibility
UPDATE_INTERVAL = 0.5  # Send updates every 0.5 seconds to ensure reliability


class BombBot(BaseBot):
    def __init__(self):
        """
        Initialize the bomb bot.
        """
        super().__init__(
            overlay_ws_url=OVERLAY_WS,
            prefix='!',
            channel_name="Feer",
            require_client_id=True
        )
        # Track recent chatters
        self.recent_chatters = deque(maxlen=PASS_MESSAGE_COUNT)  # Keep last 200 chatters
        self.current_bomb_holder = None  # Username of current bomb holder
        self.previous_bomb_holder = None  # Username of previous bomb holder (to prevent passing back)
        self.timer_start_time = None  # When the timer started
        self.timer_task = None  # Background task for timer countdown
        self.update_task = None  # Background task for constant updates
        self.game_active = False
        self.pass_count = 0  # Track how many times the bomb has been passed
        self.current_ban_duration = INITIAL_BAN_DURATION  # Current ban duration

    async def event_ready(self):
        """Called when the bot is ready and connected to Twitch."""
        await super().event_ready()
        # Start the update task to constantly send updates
        self.update_task = asyncio.create_task(self.constant_update_loop())

    async def constant_update_loop(self):
        """Constantly send updates to the overlay to ensure no missed updates."""
        while True:
            try:
                if self.game_active:
                    await self.send_bomb_update()
                await asyncio.sleep(UPDATE_INTERVAL)
            except Exception as e:
                logger.error(f"Error in constant update loop: {e}")
                await asyncio.sleep(UPDATE_INTERVAL)

    async def send_bomb_update(self):
        """Send current bomb state to overlay."""
        if self.current_bomb_holder is None:
            return
        
        # Calculate remaining time (always based on 60 second timer)
        if self.timer_start_time:
            elapsed = (datetime.now(timezone.utc) - self.timer_start_time).total_seconds()
            remaining = max(0, TIMER_DURATION - elapsed)
        else:
            remaining = TIMER_DURATION
        
        payload = {
            "type": "bomb_update",
            "holder": self.current_bomb_holder,
            "timer": round(remaining, 1),
            "game_active": self.game_active,
            "pass_count": self.pass_count,
            "ban_duration": self.current_ban_duration
        }
        
        try:
            await self.send_to_overlay(json.dumps(payload))
        except Exception as e:
            logger.error(f"Error sending bomb update: {e}")

    async def start_timer(self):
        """Start the bomb timer countdown."""
        self.timer_start_time = datetime.now(timezone.utc)
        
        # Cancel existing timer task if any
        if self.timer_task:
            self.timer_task.cancel()
        
        # Start new timer task
        self.timer_task = asyncio.create_task(self.timer_countdown())

    async def timer_countdown(self):
        """Countdown timer that bans the user if it reaches zero."""
        try:
            # Always wait 60 seconds (fixed timer duration)
            await asyncio.sleep(TIMER_DURATION)
            
            # Timer expired - ban the user with current ban duration
            if self.current_bomb_holder and self.game_active:
                logger.info(f"Timer expired! Banning {self.current_bomb_holder} for {self.current_ban_duration} seconds (pass count: {self.pass_count})")
                user_id = await self.get_user_id(self.current_bomb_holder)
                if user_id:
                    await self.timeout_user(
                        user_id, 
                        self.current_bomb_holder, 
                        duration=self.current_ban_duration, 
                        reason="Bomb timer expired"
                    )
                
                # Store loser info before resetting
                loser_username = self.current_bomb_holder
                loser_ban_duration = self.current_ban_duration
                loser_pass_count = self.pass_count
                
                # Reset the game
                self.current_bomb_holder = None
                self.previous_bomb_holder = None
                self.timer_start_time = None
                self.game_active = False
                self.pass_count = 0
                self.current_ban_duration = INITIAL_BAN_DURATION
                
                # Send game over screen to overlay
                await self.send_to_overlay(json.dumps({
                    "type": "bomb_exploded",
                    "loser": loser_username,
                    "ban_duration": loser_ban_duration,
                    "pass_count": loser_pass_count
                }))
        except asyncio.CancelledError:
            # Timer was cancelled (bomb was passed)
            pass
        except Exception as e:
            logger.error(f"Error in timer countdown: {e}")

    def get_eligible_chatters(self, for_initial_selection=False):
        """
        Get list of eligible chatters for bomb selection/passing.
        
        Args:
            for_initial_selection: If True, use last 5 messages. If False, use last 200.
            
        Returns:
            list: List of unique usernames who have chatted
        """
        if for_initial_selection:
            # Use last 5 messages for initial selection
            eligible = list(self.recent_chatters)[-INITIAL_SELECTION_MESSAGE_COUNT:]
        else:
            # Use last 200 messages for passing
            eligible = list(self.recent_chatters)
        
        # Get unique usernames
        unique_chatters = list(set(eligible))
        
        # Remove broadcaster from eligible chatters (case-insensitive)
        broadcaster_name = self.channel_name.lower()
        unique_chatters = [u for u in unique_chatters if u.lower() != broadcaster_name]
        
        # Remove nightbot from eligible chatters (case-insensitive)
        unique_chatters = [u for u in unique_chatters if u.lower() != "nightbot"]
        
        # Remove current and previous holder if not initial selection
        if not for_initial_selection:
            if self.current_bomb_holder in unique_chatters:
                unique_chatters.remove(self.current_bomb_holder)
            if self.previous_bomb_holder and self.previous_bomb_holder in unique_chatters:
                unique_chatters.remove(self.previous_bomb_holder)
        
        return unique_chatters

    async def select_random_bomb_holder(self):
        """Randomly select a bomb holder from eligible chatters."""
        logger.debug("Selecting random bomb holder...")
        eligible = self.get_eligible_chatters(for_initial_selection=True)
        logger.debug(f"Eligible chatters for selection: {eligible} (count: {len(eligible)})")
        
        if not eligible:
            logger.warning("No eligible chatters for bomb selection")
            return None
        
        selected = random.choice(eligible)
        logger.info(f"🎲 Randomly selected {selected} to receive the bomb")
        return selected

    def calculate_ban_duration(self, pass_count: int) -> int:
        """Calculate ban duration based on pass count using exponential growth."""
        duration = INITIAL_BAN_DURATION * (BAN_MULTIPLIER ** pass_count)
        return min(int(duration), MAX_BAN_DURATION)
    
    async def give_bomb_to(self, username: str):
        """Give the bomb to a specific user."""
        logger.info(f"🔥 GIVING BOMB TO: {username}")
        logger.debug(f"Previous holder: {self.previous_bomb_holder}, Current holder: {self.current_bomb_holder}")
        
        # If this is a pass (not initial selection), increment pass count and calculate ban duration
        if self.current_bomb_holder is not None:
            # This is a pass, not initial selection
            self.pass_count += 1
            self.current_ban_duration = self.calculate_ban_duration(self.pass_count)
            logger.info(f"Bomb passed! Pass count: {self.pass_count}, Ban duration increased to: {self.current_ban_duration}s")
        else:
            # Initial selection - reset to initial values
            self.pass_count = 0
            self.current_ban_duration = INITIAL_BAN_DURATION
            logger.info(f"Initial bomb selection. Ban duration: {self.current_ban_duration}s")
        
        # Update holders
        self.previous_bomb_holder = self.current_bomb_holder
        self.current_bomb_holder = username
        
        # Reset timer (always 60 seconds)
        logger.debug("Starting bomb timer...")
        await self.start_timer()
        self.game_active = True
        logger.debug(f"Game active set to: {self.game_active}")
        
        # Send update to overlay (will trigger TTS on HTML side)
        logger.debug("Sending bomb update to overlay...")
        await self.send_bomb_update()
        logger.info(f"✅ Bomb successfully given to {username}, timer started at 60s, ban duration: {self.current_ban_duration}s")

    async def event_message(self, message):
        """Handle incoming messages."""
        if message.echo:
            return
        
        username = message.author.display_name
        content = message.content.strip()
        
        # Track all chatters (for eligibility)
        self.recent_chatters.append(username)
        logger.debug(f"Message from {username}: {content}")
        # Log unique chatters count periodically (every 10 messages to avoid spam)
        if len(self.recent_chatters) % 10 == 0:
            logger.debug(f"Total recent chatters tracked: {len(self.recent_chatters)}, Unique: {len(set(self.recent_chatters))}")
        
        # Process commands first (this is needed for !ignite to work)
        await self.handle_commands(message)
        
        # Handle "pass @username" or "pass username" command (not a Twitch command, just text parsing)
        if content.lower().startswith("pass "):
            logger.debug(f"{username} attempted to pass the bomb")
            # Only the current bomb holder can pass
            if self.current_bomb_holder != username:
                logger.debug(f"{username} tried to pass but is not the current holder (holder is: {self.current_bomb_holder})")
                return
            
            # Extract target username (with or without @)
            match = re.match(r'pass @?(\w+)', content, re.IGNORECASE)
            if not match:
                logger.debug(f"Failed to parse pass command from: {content}")
                return
            
            target_username = match.group(1).lower()
            logger.debug(f"{username} wants to pass to {target_username}")
            
            # Check if target is eligible
            eligible = self.get_eligible_chatters(for_initial_selection=False)
            eligible_lower = [u.lower() for u in eligible]
            logger.debug(f"Eligible chatters for passing: {eligible}")
            
            if target_username not in eligible_lower:
                logger.info(f"{username} tried to pass to {target_username}, but they're not eligible")
                return
            
            # Find the actual username (case-insensitive match)
            actual_target = None
            for eligible_user in eligible:
                if eligible_user.lower() == target_username:
                    actual_target = eligible_user
                    break
            
            if not actual_target:
                logger.warning(f"Could not find actual target for {target_username}")
                return
            
            # Pass the bomb
            logger.info(f"🔥 BOMB PASSED: {username} passed the bomb to {actual_target}")
            await self.give_bomb_to(actual_target)

    @commands.command(name='ignite')
    async def ignite_command(self, ctx: commands.Context):
        """Start the bomb game. (Broadcaster only)"""
        logger.info(f"🔥 IGNITE command received from {ctx.author.display_name}")
        
        # Check if user is broadcaster
        is_broadcaster = getattr(ctx.author, "is_broadcaster", False)
        logger.info(f"Broadcaster check: {is_broadcaster} (user: {ctx.author.display_name}, channel: {self.channel_name})")
        if not is_broadcaster:
            logger.info(f"{ctx.author.display_name} tried to use !ignite but is not the broadcaster")
            return
        
        # Check if game is already active
        logger.info(f"Game active status: {self.game_active}")
        if self.game_active:
            logger.info("Game is already active, cannot ignite")
            return
        
        # Check if we have enough chatters
        unique_chatters = len(set(self.recent_chatters))
        logger.info(f"Recent chatters count: {len(self.recent_chatters)} messages, {unique_chatters} unique chatters")
        logger.info(f"Recent chatters list: {list(set(self.recent_chatters))}")
        
        if unique_chatters < 2:
            logger.info(f"Not enough chatters to start game (need 2, have {unique_chatters})")
            return
        
        # Get eligible chatters before selection
        eligible = self.get_eligible_chatters(for_initial_selection=True)
        logger.info(f"Eligible chatters for selection: {eligible}")
        logger.info(f"Eligible count: {len(eligible)}")
        
        # Randomly select a bomb holder and start the game
        selected = await self.select_random_bomb_holder()
        if selected:
            logger.info(f"🔥 GAME STARTED! Broadcaster ignited the game! Starting with {selected}")
            # Hide game over screen if it was showing
            await self.send_to_overlay(json.dumps({
                "type": "bomb_reset"
            }))
            await self.give_bomb_to(selected)
        else:
            logger.warning("Failed to select a bomb holder for ignition - no eligible chatters found")

    async def upon_connection(self):
        """Called when WebSocket connects - send initial state."""
        if self.game_active:
            await self.send_bomb_update()


if __name__ == "__main__":
    bot = BombBot()
    bot.run()

