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
        self.king_username = "sleazebreeze"  # Replace with the actual king's username
        self.pray_mode = False
        self.pray_streak = 0
        self.pray_high_streak = 0
        self.pray_lock = asyncio.Lock()
        self.pray_task = None

        self.polish_mode = False
        self.polish_streak = 0
        self.polish_high_streak = 0
        self.polish_lock = asyncio.Lock()
        self.polish_task = None

        # Type mode variables
        self.type_mode = False
        self.type_word = None
        self.type_streak = 0
        self.type_high_streak = 0
        self.type_lock = asyncio.Lock()
        self.type_task = None

    @commands.command(name="pray")
    async def pray_command(self, ctx: commands.Context):
        if ctx.author.display_name == self.king_username and not self.pray_mode and not self.polish_mode:
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

    @commands.command(name="polish")
    async def polish_command(self, ctx: commands.Context):
        if ctx.author.display_name == self.king_username and not self.polish_mode and not self.pray_mode:
            self.polish_mode = True
            self.polish_streak = 0
            self.polish_high_streak = 0
            await ctx.send("POLISH The King demands you polish your marble! POLISH")
            self.polish_task = asyncio.create_task(self._polish_timer(ctx))

    @commands.command(name="king")
    async def king_command(self, ctx: commands.Context):
        await ctx.send(f"All Hail the King of Marbles: @{self.king_username}")

    async def _polish_timer(self, ctx):
        await asyncio.sleep(30)
        async with self.polish_lock:
            if self.polish_mode:
                await ctx.send(f"Polishing session complete! Highest streak: {self.polish_high_streak}")
                self.polish_mode = False
                self.polish_streak = 0
                self.polish_high_streak = 0

    @commands.command(name="type")
    async def type_command(self, ctx: commands.Context):
        if ctx.author.display_name == self.king_username and not self.type_mode and not self.pray_mode and not self.polish_mode:
            args = ctx.message.content.split()
            if len(args) < 2:
                await ctx.send("Usage: !type <word>")
                return
            self.type_word = args[1]
            self.type_mode = True
            self.type_streak = 0
            self.type_high_streak = 0
            await ctx.send(f"TYPE CHALLENGE! The King demands you type: {self.type_word}")
            self.type_task = asyncio.create_task(self._type_timer(ctx))

    async def _type_timer(self, ctx):
        await asyncio.sleep(30)
        async with self.type_lock:
            if self.type_mode:
                await ctx.send(f"Type session complete! Highest streak: {self.type_high_streak}")
                self.type_mode = False
                self.type_word = None
                self.type_streak = 0
                self.type_high_streak = 0

    def timeout_seconds(self, streak: int) -> int:
        timeout = 7.5 * (2 ** streak) + 1
        return min(timeout, 86400)  # 24-hour cap

    async def event_message(self, message):
        if message.echo or message.author.display_name == "Nightbot" or (hasattr(message.author, "is_mod") and message.author.is_mod):
            return
        # Pray mode logic
        if self.pray_mode and message.author and message.author.display_name != self.king_username:
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
        
        # Polish mode logic
        if self.polish_mode and message.author and message.author.display_name != self.king_username:
            content = message.content.strip()
            username = message.author.display_name
            user_id = message.author.id
            async with self.polish_lock:
                if content.startswith("POLISH"):
                    self.polish_streak += 1
                    if self.polish_streak > self.polish_high_streak:
                        self.polish_high_streak = self.polish_streak
                else:
                    streak_broken = self.polish_streak
                    timeout_duration = self.timeout_seconds(streak_broken)
                    await message.channel.send(f"POLISH {streak_broken} ReallyMad @{username} POLISH")
                    await self.timeout_user(user_id, username, duration=timeout_duration, reason=f"Broke POLISH streak of {streak_broken}")
                    self.polish_streak = 0

        # Type mode logic
        if self.type_mode and message.author and message.author.display_name != self.king_username:
            content = message.content.strip()
            username = message.author.display_name
            user_id = message.author.id
            async with self.type_lock:
                if content.startswith(self.type_word):
                    self.type_streak += 1
                    if self.type_streak > self.type_high_streak:
                        self.type_high_streak = self.type_streak
                else:
                    streak_broken = self.type_streak
                    timeout_duration = self.timeout_seconds(streak_broken)
                    await message.channel.send(f"{self.type_word} {streak_broken} ReallyMad @{username} {self.type_word}")
                    await self.timeout_user(user_id, username, duration=timeout_duration, reason=f"Broke TYPE streak of {streak_broken}")
                    self.type_streak = 0

        await self.handle_commands(message)

if __name__ == "__main__":
    bot = KingBot()
    bot.run() 