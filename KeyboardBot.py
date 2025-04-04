import os
import asyncio
import websockets
import twitchio
from twitchio.ext import commands
import keyboard
import time
import re
import requests
import pygetwindow as gw

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


class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            token=TOKEN,
            prefix=PREFIX,
            initial_channels=CHANNELS
        )

    async def event_ready(self):
        print(f'Logged in as {self.nick}')

    async def event_message(self, message):
        if message.echo:
            return
        
        if message.content.lower() in valid_keys:
            if is_wow_focused():
                keyboard.press_and_release(message.content.lower())
                print(message.content.lower())
            else:
                print(f'Wow not focused:{message.content}')

        # if index == -1:
        #     print(f'(not a quick chat):{message.content}')
        #     return


if __name__ == '__main__':
    bot = Bot()
    bot.run()
