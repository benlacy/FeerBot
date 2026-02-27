import asyncio
import logging
import json
import random
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
TIMER_DURATION = 30  # 30s countdown; at 0 the holder gets real VIP and game ends
INITIAL_SELECTION_MESSAGE_COUNT = 5  # Last 5 messages for initial selection
STEAL_MESSAGE_COUNT = 200  # Last 200 messages for steal eligibility
MAX_STEALS_PER_USER = 3
UPDATE_INTERVAL = 0.5  # Send updates every 0.5 seconds to ensure reliability


class GoldenVipBot(BaseBot):
    def __init__(self):
        """
        Initialize the Golden VIP bot.
        VIP is passed by typing "steal" to take it. Each user can steal at most 5 times.
        """
        super().__init__(
            overlay_ws_url=OVERLAY_WS,
            prefix='!',
            channel_name="Feer",
            require_client_id=True
        )
        self.recent_chatters = deque(maxlen=STEAL_MESSAGE_COUNT)
        self.current_vip_holder = None
        self.steal_counts = {}  # username_lower -> number of steals used (max 5)
        self.timer_start_time = None
        self.timer_task = None
        self.update_task = None
        self.game_active = False

    async def event_ready(self):
        """Called when the bot is ready and connected to Twitch."""
        await super().event_ready()
        self.update_task = asyncio.create_task(self.constant_update_loop())

    async def constant_update_loop(self):
        """Constantly send updates to the overlay."""
        while True:
            try:
                if self.game_active and self.current_vip_holder is not None:
                    await self.send_vip_update()
                await asyncio.sleep(UPDATE_INTERVAL)
            except Exception as e:
                logger.error(f"Error in constant update loop: {e}")
                await asyncio.sleep(UPDATE_INTERVAL)

    async def send_vip_update(self):
        """Send current VIP state and timer to overlay."""
        if self.current_vip_holder is None:
            return
        if self.timer_start_time:
            elapsed = (datetime.now(timezone.utc) - self.timer_start_time).total_seconds()
            remaining = max(0, TIMER_DURATION - elapsed)
        else:
            remaining = TIMER_DURATION
        payload = {
            "type": "vip_update",
            "holder": self.current_vip_holder,
            "timer": round(remaining, 1),
            "game_active": self.game_active
        }
        try:
            await self.send_to_overlay(json.dumps(payload))
        except Exception as e:
            logger.error(f"Error sending vip update: {e}")

    async def start_timer(self):
        """Start the 1-minute countdown. When it hits 0, holder gets real VIP and game ends."""
        self.timer_start_time = datetime.now(timezone.utc)
        if self.timer_task:
            self.timer_task.cancel()
        self.timer_task = asyncio.create_task(self.timer_countdown())

    async def timer_countdown(self):
        """Wait 1 minute; if no steal, grant real VIP to holder and end game."""
        try:
            await asyncio.sleep(TIMER_DURATION)
            if not self.current_vip_holder or not self.game_active:
                return
            winner = self.current_vip_holder
            logger.info(f"Timer expired! Granting real VIP to {winner} and ending game.")
            user_id = await self.get_user_id(winner)
            if user_id:
                ok = await self.add_vip(user_id, winner)
                if ok:
                    logger.info(f"Successfully granted VIP to {winner}")
                else:
                    logger.error(f"Failed to grant VIP to {winner}")
            else:
                logger.error(f"Could not get user id for {winner}")
            await self.send_to_overlay(json.dumps({
                "type": "vip_winner",
                "winner": winner
            }))
            self._reset_game()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in timer countdown: {e}")

    def _reset_game(self):
        """Clear game state (no overlay message)."""
        self.current_vip_holder = None
        self.timer_start_time = None
        self.game_active = False
        if self.timer_task:
            self.timer_task.cancel()
            self.timer_task = None

    def get_eligible_chatters(self, for_initial_selection=False):
        """
        Get list of eligible chatters (same logic as bombBot).
        """
        if for_initial_selection:
            eligible = list(self.recent_chatters)[-INITIAL_SELECTION_MESSAGE_COUNT:]
        else:
            eligible = list(self.recent_chatters)

        unique_chatters = list(set(eligible))
        broadcaster_name = self.channel_name.lower()
        unique_chatters = [u for u in unique_chatters if u.lower() != broadcaster_name]
        unique_chatters = [u for u in unique_chatters if u.lower() != "nightbot"]

        if not for_initial_selection:
            # When stealing, exclude current holder so someone else must steal (optional - actually we allow re-steal to "keep" it)
            # Per spec: "when you type steal you take the vip for yourself" - so the stealer becomes the new holder. Don't exclude current.
            pass

        return unique_chatters

    def get_steals_remaining(self, username: str) -> int:
        """Return how many steals this user has left (0 means they can't steal)."""
        key = username.lower()
        used = self.steal_counts.get(key, 0)
        return max(0, MAX_STEALS_PER_USER - used)

    def can_steal(self, username: str) -> bool:
        """True if user is eligible and has steals remaining."""
        if self.get_steals_remaining(username) <= 0:
            return False
        eligible = self.get_eligible_chatters(for_initial_selection=False)
        eligible_lower = [u.lower() for u in eligible]
        return username.lower() in eligible_lower

    async def select_random_vip_holder(self):
        """Randomly select initial VIP holder from eligible chatters."""
        eligible = self.get_eligible_chatters(for_initial_selection=True)
        if not eligible:
            logger.warning("No eligible chatters for VIP selection")
            return None
        selected = random.choice(eligible)
        logger.info(f"Randomly selected {selected} as initial Golden VIP")
        return selected

    async def give_vip_to(self, username: str, is_steal: bool = False):
        """Give the VIP to a user. If is_steal, increment their steal count. Resets 1-min timer."""
        logger.info(f"VIP given to: {username}" + (" (steal)" if is_steal else " (initial)"))
        self.current_vip_holder = username
        self.game_active = True
        if is_steal:
            key = username.lower()
            self.steal_counts[key] = self.steal_counts.get(key, 0) + 1
            logger.info(f"{username} has used {self.steal_counts[key]}/{MAX_STEALS_PER_USER} steals")
        await self.start_timer()
        await self.send_vip_update()

    async def event_message(self, message):
        """Handle incoming messages."""
        if message.echo:
            return

        username = message.author.display_name
        content = message.content.strip()

        self.recent_chatters.append(username)

        await self.handle_commands(message)

        # Handle "steal" - take VIP for yourself (eligible chatters only, max 5 steals per user)
        if content.lower() == "steal":
            logger.debug(f"{username} attempted to steal the VIP")
            if self.current_vip_holder is None:
                logger.debug("No current VIP holder to steal from")
                return

            if not self.can_steal(username):
                remaining = self.get_steals_remaining(username)
                if remaining <= 0:
                    logger.info(f"{username} tried to steal but has no steals left (max {MAX_STEALS_PER_USER})")
                else:
                    logger.info(f"{username} tried to steal but is not eligible (not in recent chatters)")
                return

            # Find actual username (case-preserving) for display
            eligible = self.get_eligible_chatters(for_initial_selection=False)
            actual_username = None
            for u in eligible:
                if u.lower() == username.lower():
                    actual_username = u
                    break
            if not actual_username:
                actual_username = username

            logger.info(f"VIP stolen by {actual_username} from {self.current_vip_holder}")
            await self.give_vip_to(actual_username, is_steal=True)

    @commands.command(name='goldenvip')
    async def goldenvip_command(self, ctx: commands.Context):
        """Start the Golden VIP game. (Broadcaster only)"""
        logger.info(f"Golden VIP command received from {ctx.author.display_name}")

        is_broadcaster = getattr(ctx.author, "is_broadcaster", False)
        if not is_broadcaster:
            logger.info(f"{ctx.author.display_name} tried to use !goldenvip but is not the broadcaster")
            return

        if self.current_vip_holder is not None:
            logger.info("VIP game already active")
            return

        unique_chatters = len(set(self.recent_chatters))
        if unique_chatters < 2:
            logger.info(f"Not enough chatters to start (need 2, have {unique_chatters})")
            return

        eligible = self.get_eligible_chatters(for_initial_selection=True)
        selected = await self.select_random_vip_holder()
        if selected:
            logger.info(f"Golden VIP game started with {selected}")
            await self.send_to_overlay(json.dumps({"type": "vip_reset"}))
            await self.give_vip_to(selected, is_steal=False)
        else:
            logger.warning("Failed to select initial VIP holder")

    @commands.command(name='endvip')
    async def endvip_command(self, ctx: commands.Context):
        """End the Golden VIP game. (Broadcaster only)"""
        is_broadcaster = getattr(ctx.author, "is_broadcaster", False)
        if not is_broadcaster:
            return
        if self.current_vip_holder is None:
            return
        logger.info(f"Golden VIP game ended by broadcaster. Last holder: {self.current_vip_holder}")
        self._reset_game()
        await self.send_to_overlay(json.dumps({"type": "vip_reset"}))

    async def upon_connection(self):
        """Called when WebSocket connects - send initial state."""
        if self.game_active and self.current_vip_holder is not None:
            await self.send_vip_update()


if __name__ == "__main__":
    bot = GoldenVipBot()
    bot.run()
