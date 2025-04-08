import os
import vgamepad as vg
import asyncio
from twitchio.ext import commands

# === Environment Variables ===
TOKEN = os.getenv("TWITCH_BOT_ACCESS_TOKEN")
CLIENT_ID = os.getenv("TWITCH_APP_CLIENT_ID")
CLIENT_SECRET = os.getenv("TWITCH_APP_CLIENT_SECRET")
CHANNEL_NAME = "Feer"
PREFIX = '!'
CHANNELS = [CHANNEL_NAME]

if not TOKEN or not CLIENT_ID or not CLIENT_SECRET:
    print("FATAL ERROR: TOKEN ENV NOT SET")
    exit()

# === Virtual Gamepads ===
p1_gamepad = vg.VX360Gamepad()
p2_gamepad = vg.VX360Gamepad()

# === Command Maps ===
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
    "jump": lambda gp, duration: press_and_release_button(gp, vg.XUSB_BUTTON.XUSB_GAMEPAD_A, duration),
    "boost": lambda gp, duration: press_and_release_button(gp, vg.XUSB_BUTTON.XUSB_GAMEPAD_B, duration),
    "ballcam": lambda gp, duration: press_and_release_button(gp, vg.XUSB_BUTTON.XUSB_GAMEPAD_Y, duration),
    "drive": lambda gp, duration: press_and_release_trigger(gp, "rt", duration),
    "reverse": lambda gp, duration: press_and_release_trigger(gp, "lt", duration)
}

# === Helpers ===
def is_on_team1(name):
    if not name:
        return False
    return 'a' <= name[0].lower() <= 'm'

def is_on_team2(name):
    return not is_on_team1(name)

async def press_and_release_button(gamepad, button, press_duration=0.2):
    gamepad.press_button(button)
    gamepad.update()
    await asyncio.sleep(press_duration)
    gamepad.release_button(button)
    gamepad.update()

async def press_and_release_trigger(gamepad, trigger, press_duration=0.5):
    if trigger == "rt":
        gamepad.right_trigger(value=255)
    elif trigger == "lt":
        gamepad.left_trigger(value=255)
    gamepad.update()
    await asyncio.sleep(press_duration)
    if trigger == "rt":
        gamepad.right_trigger(value=0)
    elif trigger == "lt":
        gamepad.left_trigger(value=0)
    gamepad.update()

async def apply_command(gamepad, command, arg):
    try:
        if command in press_and_release_cmd_map:
            duration = float(arg)
            if not 0.0 <= duration <= 3.0:
                duration = 0.5
            await press_and_release_cmd_map[command](gamepad, duration)

        elif command in state_change_cmd_map:
            angle = int(arg)
            if not 0 <= angle <= 100:
                angle = 100
            scalar = angle / 100
            state_change_cmd_map[command](gamepad, scalar)
            gamepad.update()
    except (ValueError, TypeError):
        print(f"Invalid argument: {arg} for command: {command}")

# === Twitch Bot ===
class TwitchBot(commands.Bot):
    def __init__(self):
        super().__init__(token=TOKEN, prefix=PREFIX, initial_channels=CHANNELS)

    async def event_ready(self):
        print(f'Logged in as | {self.nick}')

    async def event_message(self, message):
        if message.echo:
            return

        parts = message.content.lower().split()
        if not parts:
            return

        cmd = parts[0]
        args = parts[1:]

        if message.author.display_name == "Feer" and cmd == "start":
            asyncio.create_task(press_and_release_button(p2_gamepad, vg.XUSB_BUTTON.XUSB_GAMEPAD_START))

        if cmd in state_change_cmd_map or cmd in press_and_release_cmd_map:
            arg = args[0] if args else 100
            if is_on_team1(message.author.display_name):
                asyncio.create_task(apply_command(p1_gamepad, cmd, arg))
                print(f'{message.content.lower()} **P1 Controller Interaction**')
            elif is_on_team2(message.author.display_name):
                asyncio.create_task(apply_command(p2_gamepad, cmd, arg))
                print(f'{message.content.lower()} **P2 Controller Interaction**')
        else:
            print(f'{message.content.lower()}')

# === Run Bot ===
if __name__ == '__main__':
    bot = TwitchBot()
    asyncio.run(bot.run())
