import os
import asyncio
import websockets
import logging
import re
from twitchio.ext import commands
from typing import Optional, Dict
from dotenv import load_dotenv
from pathlib import Path
from credential_manager import CredentialManager
import aiohttp
import json
from datetime import datetime, timezone
from baseBot import BaseBot

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ScoreBot(BaseBot):
    def __init__(self, overlay_ws_url: str = "ws://localhost:6790"):
        """
        Initialize the score bot that tracks +2/-2 messages.
        
        Args:
            overlay_ws_url: WebSocket URL for the overlay
        """
        super().__init__(overlay_ws_url=overlay_ws_url, prefix='!', channel_name="Feer")
        
        # Score tracking
        self.scores: Dict[str, int] = {}
        self.name_mapping: Dict[str, str] = {}  # Maps lowercase names to original capitalization
        self.broadcaster_name = "Feer"  # The broadcaster's username
        self.scores_file = Path("data/scores.json")
        
        # Load existing scores on startup
        self.load_scores()
    
    def load_scores(self):
        """Load scores from JSON file."""
        try:
            if self.scores_file.exists():
                with open(self.scores_file, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        # Handle both old format (just scores) and new format (scores + mapping)
                        if 'scores' in data and 'name_mapping' in data:
                            self.scores = data['scores']
                            self.name_mapping = data['name_mapping']
                        else:
                            # Old format - just scores, create mapping from existing names
                            self.scores = data
                            self.name_mapping = {name.lower(): name for name in data.keys()}
                logger.info(f"Loaded scores from {self.scores_file}: {self.scores}")
                logger.info(f"Loaded name mapping: {self.name_mapping}")
            else:
                logger.info(f"No existing scores file found at {self.scores_file}")
        except Exception as e:
            logger.error(f"Failed to load scores from {self.scores_file}: {e}")
            self.scores = {}
            self.name_mapping = {}
    
    def save_scores(self):
        """Save scores to JSON file."""
        try:
            # Ensure data directory exists
            self.scores_file.parent.mkdir(exist_ok=True)
            
            data = {
                'scores': self.scores,
                'name_mapping': self.name_mapping
            }
            
            with open(self.scores_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Saved scores to {self.scores_file}: {self.scores}")
            logger.debug(f"Saved name mapping: {self.name_mapping}")
        except Exception as e:
            logger.error(f"Failed to save scores to {self.scores_file}: {e}")
    
    def get_canonical_name(self, name: str) -> str:
        """
        Get the canonical name (with original capitalization) for a given name.
        If the name doesn't exist, store it with the provided capitalization.
        
        Args:
            name: The name to look up (case-insensitive)
            
        Returns:
            str: The canonical name with original capitalization
        """
        lowercase_name = name.lower()
        
        if lowercase_name not in self.name_mapping:
            # First time seeing this name, store it with original capitalization
            self.name_mapping[lowercase_name] = name
            logger.debug(f"Added new name mapping: '{lowercase_name}' -> '{name}'")
        
        return self.name_mapping[lowercase_name]
        
    def parse_score_message(self, message_content: str, author_name: str) -> Optional[tuple]:
        """
        Parse a message to extract score changes and target user.
        
        Args:
            message_content: The content of the message
            author_name: The name of the user who sent the message
            
        Returns:
            tuple: (score_change, target_user) or None if no valid score found
        """
        # Look for +2 or -2 patterns (use only the first occurrence)
        score_pattern = r'[+-]2'
        mentions_pattern = r'@(\w+)'
        
        # Find the first score change only
        first_match = re.search(score_pattern, message_content)
        if not first_match:
            return None

        match = first_match.group(0)
        total_change = 2 if match == '+2' else -2
            
        # Find mentioned users
        mentions = re.findall(mentions_pattern, message_content)
        
        # If no mentions, apply to broadcaster
        if not mentions:
            target_user = self.get_canonical_name(self.broadcaster_name)
        else:
            # Use the first mentioned user
            target_user = self.get_canonical_name(mentions[0])
        
        # Prevent users from adjusting their own score (case-insensitive)
        if author_name.lower() == target_user.lower():
            logger.info(f"Blocked {author_name} from adjusting their own score")
            return None
            
        return (total_change, target_user)
    
    def update_score(self, user: str, change: int):
        """
        Update a user's score.
        
        Args:
            user: The username to update (canonical name with original capitalization)
            change: The score change (+2 or -2)
        """
        if user not in self.scores:
            self.scores[user] = 0
        self.scores[user] += change
        
        logger.info(f"Updated {user}'s score by {change:+d}. New score: {self.scores[user]}")
        
        # Save scores to file
        self.save_scores()
        
        # Send updated scores to overlay (only if we have an event loop running)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.send_scores_to_overlay())
        except RuntimeError:
            # No event loop running (e.g., during testing), skip overlay update
            pass
    
    async def send_scores_to_overlay(self):
        """Send current scores to the overlay."""
        try:
            # Sort scores by value (highest first)
            sorted_scores = sorted(self.scores.items(), key=lambda x: x[1], reverse=True)
            
            payload = {
                "type": "scores",
                "scores": sorted_scores
            }
            
            await self.send_to_overlay(json.dumps(payload))
            logger.debug(f"Sent scores to overlay: {sorted_scores}")
            
        except Exception as e:
            logger.error(f"Failed to send scores to overlay: {e}")
    
    async def event_message(self, message):
        """
        Handle incoming messages and look for score changes.
        """
        if message.echo:
            return
            
        # Handle commands first
        await self.handle_commands(message)
            
        # Parse the message for score changes
        result = self.parse_score_message(message.content, message.author.display_name)
        if result:
            score_change, target_user = result
            self.update_score(target_user, score_change)
    
    # @commands.command(name='scores')
    # async def scores_command(self, ctx: commands.Context):
    #     """Display current scores (Mods/Broadcaster only)"""
    #     is_broadcaster = getattr(ctx.author, "is_broadcaster", False)
    #     is_mod = getattr(ctx.author, "is_mod", False)
    #     logger.info(f"User {ctx.author.display_name} is a broadcaster: {is_broadcaster}, is a mod: {is_mod}")
    #     if not (is_broadcaster or is_mod):
    #         logger.info(f"User {ctx.author.display_name} is not a broadcaster or mod, scores command ignored")
    #         return
            
    #     if not self.scores:
    #         logger.info(f"No scores recorded yet, scores command ignored")
    #         await ctx.send("No scores recorded yet.")
    #         return
            
    #     # Sort scores by value (highest first)
    #     sorted_scores = sorted(self.scores.items(), key=lambda x: x[1], reverse=True)
        
    #     score_text = "Current Scores:\n"
    #     for user, score in sorted_scores:
    #         score_text += f"{user}: {score:+d}\n"
        
    #     logger.info(f"Sending scores to {ctx.author.display_name}")
    #     await ctx.send(score_text)
    
    @commands.command(name='resetscores')
    async def reset_scores_command(self, ctx: commands.Context):
        """Reset all scores (Broadcaster only)"""
        is_broadcaster = getattr(ctx.author, "is_broadcaster", False)
        if not is_broadcaster:
            logger.info(f"User {ctx.author.display_name} is not the broadcaster, resetscores command ignored")
            return
            
        self.scores.clear()
        self.name_mapping.clear()
        self.save_scores()  # Save the cleared scores to file
        await self.send_scores_to_overlay()
        await ctx.send("All scores have been reset.")
        logger.info("Scores reset by broadcaster")
    
    async def upon_connection(self):
        """Called when WebSocket connects to overlay."""
        # Send current scores to overlay on startup
        await self.send_scores_to_overlay()
        logger.info("Sent scores to overlay on connection")

if __name__ == "__main__":
    # Load environment variables
    load_dotenv()
    
    # Create and run the bot
    bot = ScoreBot()
    bot.run()
