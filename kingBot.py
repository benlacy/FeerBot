from baseBot import BaseBot
import logging
from twitchio.ext import commands
import asyncio
import json
import base64
import requests


logger = logging.getLogger(__name__)

class KingBot(BaseBot):
    def __init__(self):
        super().__init__(
            overlay_ws_url="ws://localhost:6790",  # Use the server.py WebSocket server
            prefix='!',
            channel_name="Feer"
        )

        self.king_username = 'Birbo_'  # Replace with the actual king's username
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
        await ctx.send(f"Pray @{self.king_username} Pray KingOfTheMarbles Pray")

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
            await ctx.send(f"=====👑The King of Marbles👑=====")
            await ctx.send(f"CHAT IS IN {self.type_word} MODE FOR 30s")
            await ctx.send(f"=============================")
            self.type_task = asyncio.create_task(self._type_timer(ctx))

    async def _type_timer(self, ctx):
        await asyncio.sleep(30)
        async with self.type_lock:
            if self.type_mode:
                await ctx.send(f"=====👑The King of Marbles👑=====")
                await ctx.send(f"{self.type_word} MODE OFF. HIGHEST STREAK: {self.type_high_streak}")
                await ctx.send(f"=============================")
                # await ctx.send(f"Type session complete! Highest streak: {self.type_high_streak}")
                self.type_mode = False
                self.type_word = None
                self.type_streak = 0
                self.type_high_streak = 0

    @commands.command(name="banish")
    async def banish_command(self, ctx: commands.Context):
        if ctx.author.display_name != self.king_username:
            await ctx.send("Only the King can banish subjects!")
            return
        args = ctx.message.content.split()
        if len(args) < 2:
            await ctx.send("Usage: !banish <username>")
            return
        target_username = args[1].lstrip('@')
        user_id = await self.get_user_id(target_username)
        if not user_id:
            await ctx.send(f"Could not find user: {target_username}")
            return
        await self.timeout_user(user_id, target_username, duration=300, reason="Banished by the King!")
        await ctx.send(f"{target_username} has been banished for 5 minutes!")

    @commands.command(name="declare")
    async def declare_command(self, ctx: commands.Context):
        is_broadcaster = hasattr(ctx.author, "is_broadcaster") and ctx.author.is_broadcaster
        is_king = ctx.author.display_name == self.king_username
        
        if not (is_king or is_broadcaster):
            await ctx.send("Only the King or Broadcaster can make declarations!")
            return
        # Extract everything after "!declare"
        content = ctx.message.content.strip()
        if len(content) <= len("!declare"):
            await ctx.send("Usage: !declare <message>")
            return
        message = content[len("!declare"):].strip()
        if not message:
            await ctx.send("Usage: !declare <message>")
            return
        
        # Generate TTS on server side and send audio data to overlay (avoids CORS issues)
        audio_data_url = None
        if self.tts_api_token:
            try:
                # Generate TTS using TTS Monster API
                payload = {
                    "voice_id": self.default_voice_id,
                    "message": message[:500]  # Limit to 500 characters
                }
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": self.tts_api_token
                }
                
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
                            # Convert to base64 data URL
                            audio_base64 = base64.b64encode(audio_response.content).decode('utf-8')
                            # Determine MIME type (TTS Monster typically returns WAV)
                            mime_type = "audio/wav"  # Default, could detect from URL or content
                            audio_data_url = f"data:{mime_type};base64,{audio_base64}"
                            logger.debug("TTS audio generated and converted to base64")
                        else:
                            logger.error(f"Failed to download audio: {audio_response.status_code}")
                    else:
                        logger.error(f"TTS API error: {result}")
                else:
                    logger.error(f"TTS API request failed: {response.status_code} - {response.text}")
            except Exception as e:
                logger.error(f"Error generating TTS for overlay: {e}")
        else:
            logger.warning("TTS_MONSTER_API_TOKEN not set - TTS will not work in overlay")
        
        overlay_data = {
            "type": "declare",
            "message": message,
            "audio_data_url": audio_data_url  # Send base64 data URL instead of credentials
        }
        logger.debug(f"Sending declare overlay data: type={overlay_data['type']}, has_audio={bool(audio_data_url)}")
        await self.send_to_overlay(json.dumps(overlay_data))

    def timeout_seconds(self, streak: int) -> int:
        timeout = (7.5 * (2 ** streak)) + 1
        return min(timeout, 86400)  # 24-hour cap

    async def upon_connection(self):
        await self.send_king_to_overlay()
        pass 

    @commands.command(name="jailbreak")
    async def jailbreak_command(self, ctx: commands.Context):
        """Un-timeout all currently timed-out users. Broadcaster only."""
        is_broadcaster = hasattr(ctx.author, "is_broadcaster") and ctx.author.is_broadcaster
        if not is_broadcaster:
            return

        try:
            items = await self.get_banned_users()
            timeouts = [it for it in items if it.get('expires_at')]
            freed = 0
            for it in timeouts:
                user_id = it.get('user_id')
                username = it.get('user_login') or it.get('user_name') or str(user_id)
                if user_id:
                    await self.untimeout_user(user_id, username)
                    freed += 1
                    await asyncio.sleep(0.25)  # small delay to avoid rate limits
            await ctx.send(f"Jailbreak complete. Freed {freed} {'soul' if freed == 1 else 'souls'}.")
        except Exception as e:
            logger.error(f"Error during jailbreak: {str(e)}")
            await ctx.send("Jailbreak failed. Check logs.")

    async def event_message(self, message):
        if message.echo or message.author.display_name == "Nightbot":
            return

        is_mod = False
        if (hasattr(message.author, "is_mod") and message.author.is_mod):
            is_mod = True

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
                    await message.channel.send(f"Pray {streak_broken} KingOfTheMarbles BANNED @{username}")
                    if not is_mod:
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
                    await message.channel.send(f"POLISH {streak_broken} KingOfTheMarbles BANNED @{username}")
                    if not is_mod:
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
                    await message.channel.send(f"{self.type_word} {streak_broken} KingOfTheMarbles BANNED @{username}")
                    if not is_mod:
                        await self.timeout_user(user_id, username, duration=timeout_duration, reason=f"Broke TYPE streak of {streak_broken}")
                    self.type_streak = 0

        await self.handle_commands(message)

    async def event_ready(self):
        await super().event_ready()
        logger.debug(f"Bot is ready. Current king: {self.king_username}")

    async def send_king_to_overlay(self):
        import json
        data = {"king": self.king_username}
        await self.send_to_overlay(json.dumps(data))

        try:
            async for message in self.ws:
                try:
                    msg_data = json.loads(message)
                    if isinstance(msg_data, dict) and msg_data.get("action") == "get_king":
                        await self.send_to_overlay(json.dumps(data))
                except Exception as e:
                    logger.warning(f"Invalid king overlay message: {message} ({e})")

        except Exception as e:
            logger.error(f"Unexpected error king bot: {str(e)}", exc_info=True)

if __name__ == "__main__":
    bot = KingBot()
    bot.run() 