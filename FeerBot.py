import os
import asyncio
import websockets
import twitchio
from twitchio.ext import commands
import keyboard
import time
import re
import requests

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

class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            token=TOKEN,
            prefix=PREFIX,
            initial_channels=CHANNELS
        )
        self.ws = None  # WebSocket connection placeholder
        self.hype_train_level = 1

    async def event_ready(self):
        print(f'Logged in as {self.nick}')
        # Start the background task for periodically checking Hype Train level
        #self.loop.create_task(self.update_hype_train_periodically())

        await self.connect_websocket()  # Start WebSocket connection

    async def update_hype_train_periodically(self):
        """Periodically updates the stored Hype Train level to avoid delays in messages."""
        wait_time = 30
        while True:
            self.hype_train_level = self.get_hype_train_level()
            print(f"Updated Hype Train Level: {self.hype_train_level}")
            if self.hype_train_level > 1:
                wait_time = 5
            else:
                wait_time = 30
            await asyncio.sleep(wait_time)  # Check every 30 seconds (adjust as needed)

    async def event_message(self, message):
        if message.echo:
            return
        
        index = get_quick_chat_index(message.content)
        if index == -1:
            print(f'(not a quick chat):{message.content}')
            return

        chat_message = f'{message.author.display_name}: {quick_chat_messages[index]}'  # Prints messages to the console
        print(chat_message)
        formatted_chat_message = f'<span class="username"style="color: {message.author.color};">{message.author.display_name}</span>: <span class="message-text">{quick_chat_messages[index]}</span>' 

        for _ in range(self.hype_train_level):
            await self.send_to_overlay(formatted_chat_message)

    async def connect_websocket(self):
        """Maintains a persistent WebSocket connection."""
        while True:
            try:
                async with websockets.connect(OVERLAY_WS) as ws:
                    self.ws = ws
                    print("Connected to WebSocket overlay.")

                    # Keep the connection alive
                    while True:
                        await asyncio.sleep(1)
            except Exception as e:
                print(f"WebSocket connection error: {e}. Reconnecting in 3 seconds...")
                await asyncio.sleep(3)  # Wait before reconnecting

    async def send_to_overlay(self, text):
        """Send message using existing WebSocket connection."""
        if self.ws and (self.ws.state == websockets.State.OPEN):  # Ensure WebSocket is open
            try:
                await self.ws.send(text)
            except websockets.exceptions.ConnectionClosed:
                print("WebSocket closed. Reconnecting...")
                await self.connect_websocket()
        else:
            print("WebSocket not connected. Message not sent.")
            print("WebSocket closed. Reconnecting...")
            await self.connect_websocket()

    def get_hype_train_level(self):
        """Fetch the current Hype Train level using Twitch Helix API."""
        url = f"https://api.twitch.tv/helix/hypetrain/events?broadcaster_id={self.get_broadcaster_id()}"
        headers = {
            "Client-ID": CLIENT_ID,
            "Authorization": f"Bearer {TOKEN}"
        }
        try:
            response = requests.get(url, headers=headers)
            data = response.json()
            if "data" in data and len(data["data"]) > 0:
                return data["data"][0]["level"]
        except Exception as e:
            print(f"Error fetching Hype Train level: {e}")
        return 1  # Default to 1 if there’s no active Hype Train

    def get_broadcaster_id(self):
        return '147306920' # Feer id, dont try and find it every time
        # """Fetch the broadcaster ID for the channel."""
        # url = f"https://api.twitch.tv/helix/users?login={CHANNEL_NAME}"
        # headers = {
        #     "Client-ID": CLIENT_ID,
        #     "Authorization": f"Bearer {TOKEN}"
        # }
        # try:
        #     response = requests.get(url, headers=headers)
        #     data = response.json()
        #     if "data" in data and len(data["data"]) > 0:
        #         return data["data"][0]["id"]
        # except Exception as e:
        #     print(f"Error fetching broadcaster ID: {e}")
        # return None

    # @commands.command(name='hello')
    # async def hello_command(self, ctx: commands.Context):
    #     await ctx.send(f'Hello, {ctx.author.name}!')

if __name__ == '__main__':
    bot = Bot()
    bot.run()
