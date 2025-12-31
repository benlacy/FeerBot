import vgamepad as vg
import asyncio
from baseBot import BaseBot
import logging

logger = logging.getLogger(__name__)

# === Virtual Gamepads ===
# Gamepads will be initialized in the bot class to handle ViGEmBus connection issues


# XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP
# XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN
# XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT
# XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT

# XUSB_BUTTON.XUSB_GAMEPAD_START
# XUSB_BUTTON.XUSB_GAMEPAD_BACK

# XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB
# XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB

# XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER
# XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER

# XUSB_BUTTON.XUSB_GAMEPAD_A
# XUSB_BUTTON.XUSB_GAMEPAD_B
# XUSB_BUTTON.XUSB_GAMEPAD_X
# XUSB_BUTTON.XUSB_GAMEPAD_Y


# === Command Maps ===
state_change_cmd_map = {
    "left": lambda gp, angle: gp.left_joystick(x_value=int(-32768 * angle), y_value=0),
    "right": lambda gp, angle: gp.left_joystick(x_value=int(32767 * angle), y_value=0),
    "straight": lambda gp, angle: gp.left_joystick(x_value=0, y_value=32767),
    "forward": lambda gp, angle: gp.left_joystick(x_value=0, y_value=32767),
    "up": lambda gp, angle: gp.left_joystick(x_value=0, y_value=int(32767 * angle)),
    "down": lambda gp, angle: gp.left_joystick(x_value=0, y_value=int(-32768 * angle)),
    "neutral": lambda gp, angle: gp.left_joystick(x_value=0, y_value=0)
}



press_and_release_cmd_map = {
    "dup": lambda gp, duration: press_and_release_button(gp, vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP, 0.1),
    "ddown": lambda gp, duration: press_and_release_button(gp, vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN, 0.1),
    "dleft": lambda gp, duration: press_and_release_button(gp, vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT, 0.1),
    "dright": lambda gp, duration: press_and_release_button(gp, vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT, 0.1),
    "l1": lambda gp, duration: press_and_release_button(gp, vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER, 0.1),
    "r1": lambda gp, duration: press_and_release_button(gp, vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER, 0.1),
    "lb": lambda gp, duration: press_and_release_button(gp, vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER, 0.1),
    "rb": lambda gp, duration: press_and_release_button(gp, vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER, 0.1),
    "item": lambda gp, duration: press_and_release_button(gp, vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB, 0.1),
    "jump": lambda gp, duration: press_and_release_button(gp, vg.XUSB_BUTTON.XUSB_GAMEPAD_A, duration),
    "a": lambda gp, duration: press_and_release_button(gp, vg.XUSB_BUTTON.XUSB_GAMEPAD_A, duration),
    "boost": lambda gp, duration: press_and_release_button(gp, vg.XUSB_BUTTON.XUSB_GAMEPAD_B, duration),
    "ballcam": lambda gp, duration: press_and_release_button(gp, vg.XUSB_BUTTON.XUSB_GAMEPAD_Y, duration),
    "drive": lambda gp, duration: press_and_release_trigger(gp, "rt", duration),
    "reverse": lambda gp, duration: press_and_release_trigger(gp, "lt", duration)
}

# === Helpers ===
def is_on_team1(name):
    if not name:
        return False
    # return True
    return 'a' <= name[0].lower() <= 'm'
    # return name.lower() == 'feer'

def is_on_team2(name):
    return not is_on_team1(name)
    # return name.lower() == 'doctorfeer'

async def press_and_release_button(gamepad, button, press_duration=0.2):
    gamepad.press_button(button)
    gamepad.update()
    await asyncio.sleep(press_duration)
    gamepad.release_button(button)
    gamepad.update()

async def press_and_release_trigger(gamepad, trigger, press_duration=1):
    # gamepad.reset()
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
            if not 0.0 <= duration <= 10.0:
                duration = 0.5
            await press_and_release_cmd_map[command](gamepad, duration)

        elif command in state_change_cmd_map:
            angle = int(arg)
            if not 0 <= angle <= 100:
                angle = 100
            scalar = angle / 100
            state_change_cmd_map[command](gamepad, scalar)
            gamepad.update()
            await press_and_release_trigger(gamepad, "rt", 1)
            print(f"argument: {arg} for command: {command}")
            state_change_cmd_map["neutral"](gamepad, scalar)
            gamepad.update() 
            await asyncio.sleep(1)  # Small delay to ensure it's processed
            gamepad.update() 
    except (ValueError, TypeError):
        print(f"Invalid argument: {arg} for command: {command}")

# === Twitch Bot ===
class TwitchPlaysBot(BaseBot):
    def __init__(self):
        super().__init__(
            overlay_ws_url="ws://localhost:6790",  # Use the standard WebSocket server
            prefix='!',
            channel_name="Feer"
        )
        
        # Initialize virtual gamepads with error handling
        self.p1_gamepad = None
        self.p2_gamepad = None
        self._init_gamepads()
    
    def _init_gamepads(self):
        """Initialize virtual gamepads with proper error handling."""
        try:
            self.p1_gamepad = vg.VX360Gamepad()
            self.p2_gamepad = vg.VX360Gamepad()
            logger.info("Virtual gamepads initialized successfully")
        except AssertionError as e:
            if "ViGEmBus" in str(e):
                logger.error("ViGEmBus driver not found or not running. Please install ViGEmBus driver.")
                logger.error("Download from: https://github.com/ViGEm/ViGEmBus/releases")
                logger.error("Gamepad functionality will be disabled.")
            else:
                logger.error(f"Failed to initialize virtual gamepads: {e}")
        except Exception as e:
            logger.error(f"Unexpected error initializing gamepads: {e}")
    
    def _is_gamepad_available(self):
        """Check if gamepads are available for use."""
        return self.p1_gamepad is not None and self.p2_gamepad is not None

    async def event_message(self, message):
        if message.echo:
            return

        parts = message.content.lower().split()
        if not parts:
            return

        cmd = parts[0]
        args = parts[1:]

        # Check if gamepads are available before processing commands
        if not self._is_gamepad_available():
            if cmd in state_change_cmd_map or cmd in press_and_release_cmd_map:
                logger.warning(f"Gamepad command '{cmd}' ignored - gamepads not available")
            return

        if message.author.display_name == "Feer" and cmd == "start":
            asyncio.create_task(press_and_release_button(self.p2_gamepad, vg.XUSB_BUTTON.XUSB_GAMEPAD_START))

        if cmd in state_change_cmd_map or cmd in press_and_release_cmd_map:
            arg = args[0] if args else 100
            if is_on_team1(message.author.display_name):
                asyncio.create_task(apply_command(self.p1_gamepad, cmd, arg))
                asyncio.create_task(self.send_to_overlay(f'1{message.author.display_name}: {message.content.lower()}'))
                logger.info(f'{message.content.lower()} **P1 Controller Interaction**')
            elif is_on_team2(message.author.display_name):
                asyncio.create_task(apply_command(self.p2_gamepad, cmd, arg))
                asyncio.create_task(self.send_to_overlay(f'2{message.author.display_name}: {message.content.lower()}'))
                logger.info(f'{message.content.lower()} **P2 Controller Interaction**')
        else:
            logger.debug(f'{message.content.lower()}')

# === Run Bot ===
if __name__ == '__main__':
    bot = TwitchPlaysBot()
    asyncio.run(bot.run())
