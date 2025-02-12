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

    async def event_ready(self):
        print(f'Logged in as {self.nick}')

    async def event_message(self, message):
        if message.echo:
            return
        chat_message = f'{message.author.mention[1:]}: {message.content}'  # Prints messages to the console
        print(chat_message)

        if message.content == "You have time!":
            # Send the chat message to the WebSocket overlay
            await send_to_overlay(chat_message)

    # @commands.command(name='hello')
    # async def hello_command(self, ctx: commands.Context):
    #     await ctx.send(f'Hello, {ctx.author.name}!')

async def send_to_overlay(text):
    async with websockets.connect(OVERLAY_WS) as ws:
        await ws.send(text)

if __name__ == '__main__':
    bot = Bot()
    bot.run()
