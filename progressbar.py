import os
import asyncio
import websockets
from twitchio.ext import commands
from datetime import datetime, timezone, timedelta
import re

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


# Function to check if input matches a Quick Chat and get its index
def get_progress_bar_change(user_input):
    cleaned_input = re.sub(r'\s+', '', user_input)

    # Keywords to search for
    decrease_keywords = {"ICAN","WECAN", "-2"}#{"dsc_9341"}
    increase_keywords = {"ICANT","ICUMT","lCUMT","+2","WECANT","Utopia","LOL"}#{"dsc_1439","feerDsc1439"}

    # if cleaned_input in {"ICANT","ICUMT","lCUMT","+2","WECANT","Utopia"}:
    if any(keyword in cleaned_input for keyword in increase_keywords):
        return 5
    # if cleaned_input in {"ICAN","WECAN", "-2"}:
    if any(keyword in cleaned_input for keyword in decrease_keywords):
        return -5
    return 0

class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            token=TOKEN,
            prefix=PREFIX,
            initial_channels=CHANNELS
        )
        self.ws = None  # WebSocket connection placeholder
        self.chat_sentiment = {}
        self.total_sentiment = 50

    async def event_ready(self):
        print(f'Logged in as {self.nick}')
        # Start the background task for periodically checking Hype Train level
        #self.loop.create_task(self.update_hype_train_periodically())

        await self.connect_websocket()  # Start WebSocket connection

    async def event_message(self, message):
        if message.echo:
            return
        
        value = get_progress_bar_change(message.content)
        if value == 0:
            print(f'(not applied):{message.content}')
            return

        if message.author.display_name not in self.chat_sentiment:
            self.chat_sentiment[message.author.display_name] = [value, datetime.now(timezone.utc)]
        else:
            timediff = datetime.now(timezone.utc) - self.chat_sentiment[message.author.display_name][1]
            if (value != self.chat_sentiment[message.author.display_name][0]) or (timediff > timedelta(seconds=30)):
                print(f'NEW')
                self.chat_sentiment[message.author.display_name] = [value, datetime.now(timezone.utc)]
            else:
                # self.chat_sentiment[message.author.display_name] = [value, datetime.now(timezone.utc)]
                return

        self.total_sentiment = self.total_sentiment + value
        if self.total_sentiment < 0:
            self.total_sentiment = 0
        if self.total_sentiment > 100:
            self.total_sentiment = 100

        print(f'{str(self.total_sentiment)}')
        await self.send_to_overlay(str(self.total_sentiment))
        

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


if __name__ == '__main__':
    bot = Bot()
    bot.run()
