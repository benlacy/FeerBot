import os
import asyncio
import websockets
import logging
import json
import random
from twitchio.ext import commands
from typing import Optional, List, Set, Dict
from dotenv import load_dotenv
from pathlib import Path
from credential_manager import CredentialManager
import aiohttp
from datetime import datetime, timezone
from baseBot import BaseBot

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TypeRacerBot(BaseBot):
    def __init__(self, overlay_ws_url: str = "ws://localhost:6790"):
        """
        Initialize the TypeRacer bot.
        
        Args:
            overlay_ws_url: WebSocket URL for the overlay
        """
        super().__init__(overlay_ws_url, prefix='!', channel_name="Feer", require_client_id=True)
        
        # Game state
        self.game_active = False
        self.current_sentence = ""
        self.words = []
        self.typed_words = []
        # Track last contribution index for each user to enforce cooldown
        self.participants: Dict[str, int] = {}
        self.current_word_index = 0
        
        # Predefined sentences for the game
        self.sentences = ["I don't know how people support feer, his so ingenuine and obviously out to milk money, no idea how he even got in the position his in, hoodyhoo would wreck him so hard at casting and is 10x as good at the game as this guy. They guy doesn't even enjoy the game, it's so obvious by the way he casts. If that isn't enough check his rank and check out his twitch casting streams. I saw a few streams and this guy was so focused on advertising this meal prep sponsor......"]
        
        # Validate sentences on initialization
        original_count = len(self.sentences)
        self.sentences = [s.strip() for s in self.sentences if s and s.strip()]
        # Reduce noise; minimal validation only
        
        # Load environment variables
        load_dotenv()

    async def upon_connection(self):
        """Called when WebSocket connects to overlay."""
        # Minimal connect notice
        # Send initial state to overlay
        await self.send_to_overlay(json.dumps({
            "type": "typeracer_state",
            "game_active": self.game_active,
            "sentence": self.current_sentence,
            "words": self.words,
            "typed_words": self.typed_words,
            "current_word_index": self.current_word_index
        }))

    async def event_message(self, message):
        """Handle incoming chat messages."""
        if message.echo:
            return
        
        # Handle commands first
        await self.handle_commands(message)
            
        # Only process messages if game is active
        if not self.game_active:
            return
            
        # Get the current word to type
        if self.current_word_index >= len(self.words):
            return
            
        current_word = self.words[self.current_word_index]
        user_message = message.content.strip()
        
        # Check if user typed the correct word (case-insensitive, punctuation-aware)
        # Remove punctuation for comparison but keep original for display
        import string
        current_word_clean = current_word.lower().translate(str.maketrans('', '', string.punctuation))
        user_message_clean = user_message.lower().translate(str.maketrans('', '', string.punctuation))
        # Removed noisy prints

        # Check if user typed the word before the current word (backtracking)
        if self.current_word_index > 0:
            previous_word = self.words[self.current_word_index - 1]
            previous_word_clean = previous_word.lower().translate(str.maketrans('', '', string.punctuation))
            if user_message_clean == previous_word_clean:
                await self.speak(f"{message.author.name} messed up! Shame!")
                await self._backtrack(reason="typed_previous_word", actor=message.author.name)
                return

        # Check if user typed the word after the current word (skipping ahead)
        if self.current_word_index < len(self.words) - 1:
            next_word = self.words[self.current_word_index + 1]
            next_word_clean = next_word.lower().translate(str.maketrans('', '', string.punctuation))
            if user_message_clean == next_word_clean:
                await self.speak(f"{message.author.name} messed up! Shame!")
                await self._backtrack(reason="typed_next_word", actor=message.author.name)
                return

        # Handle correct word
        if user_message_clean == current_word_clean:
            last_index = self.participants.get(message.author.name, -1000)
            # If user is within 10-word cooldown and tries to type the current word, backtrack
            if self.current_word_index - last_index < 10:
                await self.speak(f"{message.author.name} messed up! Shame!")
                await self._backtrack(reason="cooldown_repeat", actor=message.author.name)
                return

            # Mark word as typed
            self.typed_words.append({
                "word": self.words[self.current_word_index],
                "user": message.author.name,
                "timestamp": datetime.now().isoformat()
            })

            # Record contribution index for cooldown
            self.participants[message.author.name] = self.current_word_index

            # Move to next word
            self.current_word_index += 1
            
            # Send update to overlay
            await self.send_to_overlay(json.dumps({
                "type": "word_typed",
                "word": self.words[self.current_word_index - 1],
                "user": message.author.name,
                "current_word_index": self.current_word_index,
                "total_words": len(self.words),
                "progress": self.current_word_index / len(self.words)
            }))
            # Speak only the word typed
            await self.speak(f"{self.words[self.current_word_index - 1]}")
            # Log advancement
            logger.info(f"{message.author.name} advanced with '{self.words[self.current_word_index - 1]}' -> {self.current_word_index}/{len(self.words)}")
            
            # Check if game is complete
            if self.current_word_index >= len(self.words):
                await self.complete_game()
            
        else:
            # User typed wrong word (not current, not adjacent tracked), ignore
            return

    async def complete_game(self):
        """Handle game completion."""
        self.game_active = False
        
        # Calculate completion time (rough estimate)
        if self.typed_words:
            start_time = datetime.fromisoformat(self.typed_words[0]["timestamp"])
            end_time = datetime.fromisoformat(self.typed_words[-1]["timestamp"])
            completion_time = (end_time - start_time).total_seconds()
        else:
            completion_time = 0
        
        # Send completion to overlay
        await self.send_to_overlay(json.dumps({
            "type": "game_complete",
            "completion_time": completion_time,
            "participants": list(self.participants),
            "total_participants": len(self.participants)
        }))
        
        # Reduced logging

    async def start_game(self, sentence: str = None):
        """Start a new TypeRacer game."""
        if self.game_active:
            return "Game is already active! Use !reset to start over."
        
        # Use provided sentence or pick random one
        if sentence and sentence.strip():
            self.current_sentence = sentence.strip()
        else:
            if not self.sentences:
                self.current_sentence = "Hello world this is a typing game"
            else:
                selected = random.choice(self.sentences)
                self.current_sentence = selected
        
        # Ensure we have a valid sentence
        if not self.current_sentence or not self.current_sentence.strip():
            self.current_sentence = "Hello world this is a typing game"
        
        # Split into words while preserving the original sentence
        import re
        # Split on whitespace but keep the original sentence intact
        self.words = re.findall(r'\S+', self.current_sentence)
        
        # Ensure we have words
        if not self.words:
            self.words = ["Hello", "world", "typing", "game"]
            self.current_sentence = " ".join(self.words)
        
        self.typed_words = []
        self.participants = {}
        self.current_word_index = 0
        self.game_active = True
        
        # Send game start to overlay
        await self.send_to_overlay(json.dumps({
            "type": "game_start",
            "sentence": self.current_sentence,
            "words": self.words,
            "total_words": len(self.words),
            "original_sentence": self.current_sentence
        }))
        return f"TypeRacer game started! Type the first word: '{self.words[0]}'"

    async def reset_game(self):
        """Reset the current game."""
        self.game_active = False
        self.current_sentence = ""
        self.words = []
        self.typed_words = []
        self.participants = {}
        self.current_word_index = 0
        
        # Send reset to overlay
        await self.send_to_overlay(json.dumps({
            "type": "game_reset"
        }))
        
        return "TypeRacer game reset!"

    @commands.command(name='typeracer')
    async def typeracer_command(self, ctx: commands.Context):
        """Start a new TypeRacer game. (Mods/Broadcaster only)"""
        is_broadcaster = getattr(ctx.author, "is_broadcaster", False)
        is_mod = getattr(ctx.author, "is_mod", False)
        if not (is_broadcaster or is_mod):
            return
        
        # Check if there's a custom sentence provided
        sentence = ctx.message.content.replace('!typeracer', '').strip()
        if sentence:
            sentence = sentence.strip('"').strip("'")
        
        result = await self.start_game(sentence if sentence else None)
        await ctx.send(result)

    @commands.command(name='typeracerreset')
    async def typeracer_reset_command(self, ctx: commands.Context):
        """Reset the current TypeRacer game. (Mods/Broadcaster only)"""
        is_broadcaster = getattr(ctx.author, "is_broadcaster", False)
        is_mod = getattr(ctx.author, "is_mod", False)
        if not (is_broadcaster or is_mod):
            return
        
        result = await self.reset_game()
        await ctx.send(result)

    @commands.command(name='typeracerstatus')
    async def typeracer_status_command(self, ctx: commands.Context):
        """Check the current TypeRacer game status."""
        if not self.game_active:
            await ctx.send("No TypeRacer game is currently active. Use !typeracer to start one!")
            return
        
        progress = self.current_word_index / len(self.words) * 100
        current_word = self.words[self.current_word_index] if self.current_word_index < len(self.words) else "Game Complete!"
        
        await ctx.send(f"TypeRacer Status: {self.current_word_index}/{len(self.words)} words ({progress:.1f}%) - Next word: '{current_word}'")

    @commands.command(name='typeracerhelp')
    async def typeracer_help_command(self, ctx: commands.Context):
        """Show TypeRacer game help."""
        help_text = (
            "TypeRacer Game Help:\n"
            "• Type the current word to contribute to the sentence\n"
            "• You can type again after 10 more words are completed\n"
            "• Work together to complete the entire sentence\n"
            "• Commands: !typeracer, !typeracerreset, !typeracerstatus, !typeracerhelp"
        )
        await ctx.send(help_text)

    async def _backtrack(self, reason: str, actor: str):
        """Move the current word index back by one and notify overlay."""
        if self.current_word_index <= 0:
            # Already at start; cannot backtrack further
            logger.info(f"Backtrack requested by {actor} for reason '{reason}', but at start.")
            return
        self.current_word_index -= 1
        await self.send_to_overlay(json.dumps({
            "type": "word_backtrack",
            "reason": reason,
            "actor": actor,
            "current_word_index": self.current_word_index,
            "total_words": len(self.words),
            "progress": self.current_word_index / len(self.words)
        }))
        # Log backtrack event
        reason_text = {
            "cooldown_repeat": "cooldown repeat",
            "typed_previous_word": "typed previous word",
            "typed_next_word": "typed next word"
        }.get(reason, "rule violation")
        logger.info(f"Backtrack by {actor}: {reason_text} -> {self.current_word_index}/{len(self.words)}")

async def main():
    """Main function to run the TypeRacer bot."""
    # Load environment variables
    load_dotenv()
    
    # Create and run the bot
    bot = TypeRacerBot()
    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())
