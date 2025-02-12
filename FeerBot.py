import os
import asyncio
import websockets
import twitchio
from twitchio.ext import commands

# WebSocket Server URL (to send messages to the overlay)
OVERLAY_WS = "ws://localhost:6790"

# Replace with your bot's details
TOKEN = os.getenv("TWITCH_BOT_ACCESS_TOKEN")
if TOKEN == None:
    print("FATAL ERROR: TOKEN ENV NOT SET")
    exit()

# NICK = 'FeerBot'
PREFIX = '!'
CHANNELS = ['Feer']

class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            token=TOKEN,
            prefix=PREFIX,
            initial_channels=CHANNELS
        )
        self.ws = None  # WebSocket connection placeholder

    async def event_ready(self):
        print(f'Logged in as {self.nick}')
        asyncio.create_task(self.connect_websocket())  # Start WebSocket connection

    async def event_message(self, message):
        if message.echo:
            return
        chat_message = f'{message.author.mention[1:]}: {message.content}'  # Prints messages to the console
        print(chat_message)

        #if message.content == "You have time!":
            # Send the chat message to the WebSocket overlay
        await self.send_to_overlay(chat_message)

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

    # @commands.command(name='hello')
    # async def hello_command(self, ctx: commands.Context):
    #     await ctx.send(f'Hello, {ctx.author.name}!')

if __name__ == '__main__':
    bot = Bot()
    bot.run()
