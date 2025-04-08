import os
import vgamepad as vg
import time
from queue import Queue, Empty
from twitchio.ext import commands
import asyncio

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

# === Virtual Gamepads ===
p1_gamepad = vg.VX360Gamepad()
p2_gamepad = vg.VX360Gamepad()

# === Command Map ===
state_change_cmd_map = {
    "left": lambda gp, angle: gp.left_joystick(x_value=int(-32768 * angle), y_value=0),
    "right": lambda gp, angle: gp.left_joystick(x_value=int(32767 * angle), y_value=0),
    "straight": lambda gp, angle: gp.left_joystick(x_value=0, y_value=32767),
    "forwards": lambda gp, angle: gp.left_joystick(x_value=0, y_value=32767),
    "up": lambda gp, angle: gp.left_joystick(x_value=0, y_value=int(32767 * angle)),
    "down": lambda gp, angle: gp.left_joystick(x_value=0, y_value=int(-32768 * angle)),
    "neutral": lambda gp, angle: gp.left_joystick(x_value=0, y_value=0)
}

press_and_release_cmd_map = {
    # Button Presses with a delay between press and release
    "jump": lambda gp, duration: press_and_release_button(gp, vg.XUSB_BUTTON.XUSB_GAMEPAD_A),
    "boost": lambda gp, duration: press_and_release_button(gp, vg.XUSB_BUTTON.XUSB_GAMEPAD_B, 0.5),
    "ballcam": lambda gp, duration: press_and_release_button(gp, vg.XUSB_BUTTON.XUSB_GAMEPAD_Y),
    # "start": lambda gp: press_and_release_button(gp, vg.XUSB_BUTTON.XUSB_GAMEPAD_START),

    # Trigger Presses with delay
    "drive": lambda gp, duration: press_and_release_trigger(gp, "rt", duration),
    "reverse": lambda gp, duration: press_and_release_trigger(gp, "lt", duration)
}

def is_on_team1(name):
    if not name:
        return False  # or handle however you'd like
    first_letter = name[0].lower()
    return first_letter >= 'a' and first_letter <= 'm'

def is_on_team2(name):
    return not is_on_team1(name)


# === Button Press and Release with Delay ===
async def press_and_release_button(gamepad, button, press_duration=0.2):
    # Press the button
    gamepad.press_button(button)
    gamepad.update()  # Update gamepad state
    await asyncio.sleep(press_duration)  # Wait for the specified duration
    
    # Release the button
    gamepad.release_button(button)
    gamepad.update()  # Update gamepad state again

# === Trigger Press and Release with Delay ===
async def press_and_release_trigger(gamepad, trigger, press_duration=0.5):
    if trigger == "rt":
        gamepad.right_trigger(value=255)  # Activate the right trigger (Drive)
    elif trigger == "lt":
        gamepad.left_trigger(value=255)   # Activate the left trigger (Reverse)
    
    gamepad.update()  # Update gamepad state
    await asyncio.sleep(press_duration)  # Wait for the specified duration
    
    # Deactivate the trigger after the duration
    if trigger == "rt":
        gamepad.right_trigger(value=0)
    elif trigger == "lt":
        gamepad.left_trigger(value=0)
    
    gamepad.update()  # Update gamepad state again

async def apply_command(gamepad, command, arg):
    try:
        if command in press_and_release_cmd_map:
            duration = float(arg)
            if not (0.0 <= duration <= 3.0):
                duration = 0.5  # fallback to default
            await press_and_release_cmd_map[command](gamepad, duration)

        elif command in state_change_cmd_map:
            angle = int(arg)
            if not (0 <= angle <= 100):
                angle = 100  # fallback to full input
            scalar = angle / 100
            state_change_cmd_map[command](gamepad, scalar)
            gamepad.update()
    except (ValueError, TypeError):
        print(f"Invalid argument: {arg} for command: {command}")

# === Twitch Bot Class ===
class TwitchBot(commands.Bot):
    def __init__(self):
        super().__init__(
            token=TOKEN,
            prefix=PREFIX,
            initial_channels=CHANNELS
        )

    async def event_ready(self):
        print(f'Logged in as | {self.nick}')
        # print(f'Pressing Start in 10s')
        # time.sleep(10)
        # await press_and_release_button(p2_gamepad, vg.XUSB_BUTTON.XUSB_GAMEPAD_START)

    async def event_message(self, message):
        if message.echo:
            return
        parts = message.content.lower().split()
        if not parts:
            return
        
        cmd = parts[0]
        args = parts[1:]

        # Only I can add an extra player
        if (message.author.display_name == "Feer") and (cmd == "start"):
            asyncio.create_task(press_and_release_button(p2_gamepad, vg.XUSB_BUTTON.XUSB_GAMEPAD_START))

        if (cmd in state_change_cmd_map) or (cmd in press_and_release_cmd_map):
            if is_on_team1(message.author.display_name):
                asyncio.create_task(apply_command(p1_gamepad, cmd, args[0] if args else 100))
                print(f'{message.content.lower()} **P1 Controller Interation**')
            if is_on_team2(message.author.display_name):
                asyncio.create_task(apply_command(p2_gamepad, cmd, args[0] if args else 100))
                print(f'{message.content.lower()} **P2 Controller Interation**')

            
        else:
            print(f'{message.content.lower()}')

# === Run Bot and Queue Processor ===
if __name__ == '__main__':
    bot = TwitchBot()
    loop = asyncio.get_event_loop()
    # loop.create_task(process_queues())
    loop.run_until_complete(bot.run())
