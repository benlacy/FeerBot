"""
adviceBot - Logs Twitch chat and uses OpenAI to generate Rocket League advice every 10 minutes.
"""
import asyncio
import base64
import json
import logging
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from twitchio.ext import commands
from baseBot import BaseBot

# Ensure .env is loaded from project directory (CredentialManager may load before cwd is set)
load_dotenv(Path(__file__).resolve().parent / ".env")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

OVERLAY_WS = "ws://localhost:6790"
ADVICE_INTERVAL_SECONDS = 30  # 10 minutes
MAX_CHAT_MESSAGES = 500  # Keep last 500 messages to avoid token limits
MAX_ADVICE_HISTORY_TURNS = 5  # Keep last N user/assistant pairs for context
SYSTEM_PROMPT = (
    "tell me a story about a cat in a bag. it should be an extremely intricate story but told only one sentence at a time."
)


class AdviceBot(BaseBot):
    def __init__(self):
        super().__init__(
            overlay_ws_url=OVERLAY_WS,
            prefix='!',
            channel_name="Feer",
            require_client_id=True
        )
        self.chat_log: deque = deque(maxlen=MAX_CHAT_MESSAGES)
        self.advice_history: list[dict] = []  # Conversation history for context
        self.advice_task = None
        self._advice_lock = asyncio.Lock()

    async def event_ready(self):
        """Called when the bot is ready and connected to Twitch."""
        await super().event_ready()
        self.advice_task = asyncio.create_task(self._advice_loop())
        logger.info("AdviceBot ready. Logging chat and generating advice every 10 minutes.")

    async def _advice_loop(self):
        """Every 10 minutes, take logged chat and generate advice via OpenAI."""
        while True:
            try:
                await asyncio.sleep(ADVICE_INTERVAL_SECONDS)
                await self._generate_and_display_advice()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in advice loop: {e}")
                await asyncio.sleep(60)  # Brief pause before retrying

    def _format_chat_for_prompt(self) -> str:
        """Format logged chat messages for the OpenAI prompt."""
        if not self.chat_log:
            return "(No chat messages logged in this period.)"
        lines = []
        for entry in self.chat_log:
            username = entry.get("username", "?")
            content = entry.get("content", "")
            lines.append(f"{username}: {content}")
        return "\n".join(lines)

    async def _call_openai(self, chat_text: str) -> str | None:
        """Call OpenAI API to generate advice. Builds on previous advice for continuity."""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.error("OPENAI_API_KEY not set. Cannot generate advice.")
            return None

        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=api_key)

            user_content = (
                #"Twitch chats are your inner thoughts. Use them to communicate with me. Your response should be very short. Like 30 words max basically"
                # "Please analyze them and give me actionable Rocket League advice based on what chat is saying "
                # "or any patterns you notice. Keep it concise. Two sentences max. Also your messages should read like a person who is talking to me. "
                # "You should summarize chat almost as a priority and relay what they are saying, make it a rocket league context if possible. "
                # "Everything you say should be condensending or even rude in order to be funny. "
                # "Build on what you've already told me - don't repeat yourself, reference earlier advice if relevant.\n\n"
                #"Twitch chat:\n"
                f"{chat_text}"
            )

            # Build messages: system + history + new user message
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            messages.extend(self.advice_history[-MAX_ADVICE_HISTORY_TURNS * 2 :])  # Last N turns
            messages.append({"role": "user", "content": user_content})

            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=500,
            )
            content = response.choices[0].message.content
            advice = content.strip() if content else None

            if advice:
                # Append this turn to history for next time
                self.advice_history.append({"role": "user", "content": user_content})
                self.advice_history.append({"role": "assistant", "content": advice})

            return advice
        except ImportError:
            logger.error("OpenAI package not installed. Run: pip install openai")
            return None
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            return None

    async def _generate_and_display_advice(self):
        """Generate advice from logged chat and send to overlay + log."""
        async with self._advice_lock:
            chat_text = self._format_chat_for_prompt()
            advice = await self._call_openai(chat_text)

            if not advice:
                advice = "Could not generate advice this round. Make sure OPENAI_API_KEY is set and the OpenAI package is installed."

            logger.info("=" * 60)
            logger.info("ROCKET LEAGUE ADVICE (from Twitch chat analysis):")
            logger.info("-" * 60)
            for line in advice.split("\n"):
                logger.info(line)
            logger.info("=" * 60)

            # Generate TTS via TTS Monster (like declare_overlay)
            audio_data_url = None
            if self.tts_api_token:
                try:
                    payload_tts = {
                        "voice_id": "c4ad44ae-8da9-4375-90c0-55a1e6f1fbc6",#self.default_voice_id,#"c4ad44ae-8da9-4375-90c0-55a1e6f1fbc6",
                        "message": advice[:500]  # TTS Monster limit
                    }
                    headers = {
                        "Content-Type": "application/json",
                        "Authorization": self.tts_api_token
                    }
                    response = requests.post(
                        self.tts_api_url,
                        headers=headers,
                        data=json.dumps(payload_tts)
                    )
                    if response.status_code == 200:
                        result = response.json()
                        if result.get("status") == 200:
                            audio_url = result.get("url")
                            audio_response = requests.get(audio_url)
                            if audio_response.status_code == 200:
                                audio_base64 = base64.b64encode(audio_response.content).decode('utf-8')
                                audio_data_url = f"data:audio/wav;base64,{audio_base64}"
                    else:
                        logger.warning(f"TTS Monster request failed: {response.status_code}")
                except Exception as e:
                    logger.error(f"Error generating TTS for advice: {e}")

            payload = {
                "type": "advice",
                "message": advice,
                "audio_data_url": audio_data_url,
            }
            try:
                await self.send_to_overlay(json.dumps(payload))
            except Exception as e:
                logger.error(f"Failed to send advice to overlay: {e}")

            # Clear the log after generating advice so next period starts fresh
            self.chat_log.clear()

    async def event_message(self, message):
        """Log all non-echo chat messages."""
        if message.echo:
            return

        username = message.author.display_name
        content = message.content.strip()

        # Skip bot messages
        if username.lower() == "nightbot":
            return

        self.chat_log.append({
            "username": username,
            "content": content,
        })

        await self.handle_commands(message)

    @commands.command(name='advice')
    async def advice_command(self, ctx):
        """Manually trigger advice generation. (Broadcaster/Mod only)"""
        is_broadcaster = getattr(ctx.author, "is_broadcaster", False)
        is_mod = getattr(ctx.author, "is_mod", False)
        if not (is_broadcaster or is_mod):
            return
        logger.info(f"Manual advice trigger from {ctx.author.display_name}")
        await self._generate_and_display_advice()


if __name__ == "__main__":
    bot = AdviceBot()
    bot.run()
