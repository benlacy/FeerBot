from baseBot import BaseBot
import logging
from twitchio.ext import commands
import asyncio

logger = logging.getLogger(__name__)

class KingBot(BaseBot):
    def __init__(self):
        super().__init__(
            overlay_ws_url=None,  # No overlay for now
            prefix='!',
            channel_name="Feer"
        )
        self.king_username = "Feer"  # Replace with the actual king's username
        self.pray_mode = False
        self.pray_streak = 0
        self.pray_high_streak = 0
        self.pray_lock = asyncio.Lock()
        self.pray_task = None

    @commands.command(name="pray")
    async def pray_command(self, ctx: commands.Context):
        if ctx.author.display_name == self.king_username and not self.pray_mode:
            self.pray_mode = True
            self.pray_streak = 0
            self.pray_high_streak = 0
            await ctx.send("Pray King of Marbles demands you pray! Pray")
            self.pray_task = asyncio.create_task(self._pray_timer(ctx))

    async def _pray_timer(self, ctx):
        await asyncio.sleep(30)
        async with self.pray_lock:
            if self.pray_mode:
                await ctx.send(f"Pray session complete! Highest streak: {self.pray_high_streak}")
                self.pray_mode = False
                self.pray_streak = 0
                self.pray_high_streak = 0

    def timeout_seconds(self, streak: int) -> int:
        timeout = 7.5 * (2 ** streak) + 1
        return min(timeout, 86400)  # 24-hour cap

    async def event_message(self, message):
        if message.echo or message.author.display_name == "Nightbot":
            return
        # Pray mode logic
        if self.pray_mode and message.author: # and message.author.display_name != self.king_username:
            content = message.content.strip()
            username = message.author.display_name
            user_id = message.author.id
            async with self.pray_lock:
                if content.startswith("Pray"):
                    self.pray_streak += 1
                    if self.pray_streak > self.pray_high_streak:
                        self.pray_high_streak = self.pray_streak
                else:
                    streak_broken = self.pray_streak
                    timeout_duration = self.timeout_seconds(streak_broken)
                    await message.channel.send(f"Pray {streak_broken} ReallyMad  @{username}")
                    await self.timeout_user(user_id, username, duration=timeout_duration, reason=f"Broke pray streak of {streak_broken}")
                    self.pray_streak = 0
        
        await self.handle_commands(message)

if __name__ == "__main__":
    bot = KingBot()
    bot.run() 