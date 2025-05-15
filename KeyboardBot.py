import os
from twitchio.ext import commands
import keyboard
import pygetwindow as gw
from baseBot import BaseBot
import logging

logger = logging.getLogger(__name__)

# Replace with your bot's details
TOKEN = os.getenv("TWITCH_BOT_ACCESS_TOKEN")
CHANNEL_NAME = "Feer"

if not TOKEN:
    print("FATAL ERROR: TOKEN ENV NOT SET")
    exit()

# NICK = 'FeerBot'
PREFIX = '!'
CHANNELS = [CHANNEL_NAME]

valid_keys = ['1','2','3','4','5','6','g','r','p','h']

game_window_name = "World of Warcraft"

def is_wow_focused():
    active_window = gw.getActiveWindow()
    return active_window and game_window_name in active_window.title

class KeyboardBot(BaseBot):
    def __init__(self):
        super().__init__(
            overlay_ws_url=None,  # This bot doesn't use WebSocket
            prefix='!',
            channel_name="Feer",
            require_client_id=False  # This bot doesn't need client ID
        )

    async def event_message(self, message):
        if message.echo:
            return
        
        if message.content.lower() in valid_keys:
            if is_wow_focused():
                keyboard.press_and_release(message.content.lower())
                logger.info(message.content.lower())
            else:
                logger.debug(f'Wow not focused:{message.content}')

if __name__ == '__main__':
    bot = KeyboardBot()
    bot.run()
