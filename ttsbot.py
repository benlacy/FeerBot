import logging
from baseBot import BaseBot
import os
import json
import random
logger = logging.getLogger(__name__)

class TTSBot(BaseBot):
    def __init__(self):
        """
        Initialize the TTS bot.
        """
        super().__init__(
            overlay_ws_url="ws://localhost:6790",
            prefix='!',
            channel_name="Feer",
            require_client_id=True
        )
        
        # TTS Master system
        self.tts_master = "Feer"  # Default TTS master
        self.master_change_chance = 100  # 1 in 100 chance
        
        logger.info("TTS Bot initialized")

    async def event_message(self, message):
        """
        Handle incoming messages for TTS functionality.
        """
        if message.echo:
            return
        
        # Check if there's a 1 in 100 chance to change TTS master
        if random.randint(1, self.master_change_chance) == 1:
            old_master = self.tts_master
            self.tts_master = message.author.display_name
            logger.info(f"TTS Master changed from {old_master} to {self.tts_master}")
            
            # Send TTS master update to overlay
            await self.send_tts_master_update()
            
            # Announce the change via TTS
            announcement = f"TTS Master has changed to {self.tts_master}!"
            await self.speakMonster(announcement)
        
        # For testing - let's also send updates more frequently to see if it works
        # Remove this after testing
        if random.randint(1, 10) == 1:  # 1 in 10 chance for testing
            logger.info(f"Sending test TTS master update: {self.tts_master}")
            await self.send_tts_master_update()
        
        # If the current message is from the TTS master, read it out
        if message.author.display_name.lower() == self.tts_master.lower():
            # Choose which TTS method to use:
            # await self.speak(message.content)        # Use gTTS
            await self.speakMonster(message.content)   # Use TTSMonster

    async def send_tts_master_update(self):
        """
        Send the current TTS master to the overlay.
        """
        try:
            if hasattr(self, 'ws') and self.ws:
                message = {
                    "tts_master": self.tts_master
                }
                await self.ws.send(json.dumps(message))
                logger.info(f"Sent TTS master update: {self.tts_master}")
        except Exception as e:
            logger.error(f"Error sending TTS master update: {e}")

    async def upon_connection(self):
        """
        Called when WebSocket connection is established.
        TTS-specific connection setup can be added here.
        """
        logger.info("TTS Bot connected to overlay")
        # Send initial TTS master
        await self.send_tts_master_update()

if __name__ == "__main__":
    # Initialize and run the bot
    bot = TTSBot()
    bot.run()
