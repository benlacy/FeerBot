import os
import asyncio
import websockets
import twitchio
from twitchio.ext import commands
import keyboard
import time
import re
import requests
from baseBot import BaseBot
import logging

logger = logging.getLogger(__name__)

# WebSocket Server URL (to send messages to the overlay)
OVERLAY_WS = "ws://localhost:6790"

# Replace with your bot's details
TOKEN = os.getenv("TWITCH_BOT_ACCESS_TOKEN")
CLIENT_ID = os.getenv("TWITCH_APP_CLIENT_ID")  # Add this to your environment variables
CLIENT_SECRET = os.getenv("TWITCH_APP_CLIENT_SECRET")
CHANNEL_NAME = "Feer"

if not TOKEN or not CLIENT_ID or not CLIENT_SECRET:
    print("FATAL ERROR: TOKEN ENV NOT SET")
    exit()

# NICK = 'FeerBot'
PREFIX = '!'
CHANNELS = [CHANNEL_NAME]

quick_chat_messages = [
    "$H@%!", "All yours.", "Bumping!", "Calculated.", "Centering!", "Close one!",
    "Defending...", "Everybody Dance!", "Faking.", "gg", "Go for it!", "Good luck!",
    "Great clear!", "Great pass!", "Holy cow!", "Here. We. Go.", "Have fun!",
    "I got it!", "In position.", "I'll do my best.", "Incoming!", "Let's do this!",
    "My bad...", "My fault.", "Need boost!", "Nice block!", "Nice bump!", "Nice cars!",
    "Nice demo!", "Nice one!", "Nice shot!", "Nice moves.", "No problem.", "No way!",
    "Noooo!", "OMG!", "Okay.", "On your left.", "On your right.", "One. More. Game.",
    "Oops!", "Passing!", "Party Up?", "Rematch!", "Rotating Back!", "Rotating Up!",
    "Savage!", "Siiiick!", "Sorry!", "Take the shot!", "That was fun!", "Thanks!",
    "This is Rocket League!", "We got this.", "Well played.", "What a play!",
    "What a save!", "What a game!", "Whew.", "Whoops...", "Wow!", "Yes!", "You have time!"
]

# Function to normalize messages (removes special characters & spaces, converts to lowercase)
def normalize(text):
    return re.sub(r'[^a-zA-Z0-9]', '', text).lower()

# Create a dictionary that maps normalized messages to their index in the list
normalized_map = {normalize(msg): i for i, msg in enumerate(quick_chat_messages)}

# Function to check if input matches a Quick Chat and get its index
def get_quick_chat_index(user_input):
    normalized_input = normalize(user_input)
    return normalized_map.get(normalized_input, -1)  # Returns -1 if not found

class QuickChatBot(BaseBot):
    def __init__(self):
        super().__init__(
            overlay_ws_url=OVERLAY_WS,
            prefix='!',
            channel_name="Feer"
        )
        self.hype_train_level = 1

    async def event_message(self, message):
        if message.echo:
            return
        
        index = get_quick_chat_index(message.content)
        if index == -1:
            logger.debug(f'(not a quick chat):{message.content}')
            return

        chat_message = f'{message.author.display_name}: {quick_chat_messages[index]}'
        logger.info(chat_message)
        formatted_chat_message = f'<span class="username"style="color: {message.author.color};">{message.author.display_name}</span>: <span class="message-text">{quick_chat_messages[index]}</span>' 

        for _ in range(self.hype_train_level):
            await self.send_to_overlay(formatted_chat_message)

    def get_hype_train_level(self):
        """Fetch the current Hype Train level using Twitch Helix API."""
        url = f"https://api.twitch.tv/helix/hypetrain/events?broadcaster_id={self.get_broadcaster_id()}"
        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.token}"
        }
        try:
            response = requests.get(url, headers=headers)
            data = response.json()
            if "data" in data and len(data["data"]) > 0:
                return data["data"][0]["level"]
        except Exception as e:
            logger.error(f"Error fetching Hype Train level: {e}")
        return 1  # Default to 1 if there's no active Hype Train

    def get_broadcaster_id(self):
        return '147306920'  # Feer id

if __name__ == '__main__':
    bot = QuickChatBot()
    bot.run()
