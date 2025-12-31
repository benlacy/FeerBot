import os
import asyncio
import websockets
import logging
import json
import aiohttp
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Configuration
RLSTATS_API_KEY = os.getenv("RLSTATS_API_KEY")
PLATFORM = os.getenv("RL_RANK_PLATFORM", "steam")  # steam, ps4, xbox, switch, epic
PLAYER_ID = os.getenv("RL_RANK_PLAYER_ID", "FeerRL")  # Your Rocket League player ID
OVERLAY_WS_URL = "ws://localhost:6790"
MMR_HISTORY_FILE = "data/rlrank_history.json"
CHECK_INTERVAL = 150  # Check every 5 minutes (300 seconds)

# Maps for platform and playlist names to their IDs
PLATFORM_MAP = {
    "steam": 1,
    "ps4": 2,
    "xbox": 3,
    "switch": 4,
    "epic": 5
}

PLAYLIST_MAP = {
    "unranked": '0',
    "duel": '10',
    "doubles": '11',  # 2v2
    "solo standard": '12',
    "standard": '13',
    "hoops": '27',
    "rumble": '28',
    "dropshot": '29',
    "snow day": '30',
    "tournament": '34'
}


def get_platform_id(platform_name: str) -> int | None:
    return PLATFORM_MAP.get(platform_name.strip().lower())


async def get_player_stats(platform_id, player_id):
    """Fetch player stats from RLStats API."""
    url = "https://api.rlstats.net/v1/profile/stats"
    params = {
        "apikey": RLSTATS_API_KEY,
        "platformid": platform_id,
        "playerid": player_id
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as response:
            if response.status == 200:
                logger.info(f"Successfully fetched stats for {player_id} on platform {platform_id}")
                return await response.json()
            else:
                logger.error(f"Failed to fetch stats: {response.status}")
                logger.error(await response.text())
                return None


async def get_player_mmr(platform_id, player_id, playlist_id=PLAYLIST_MAP['doubles']):
    """Get MMR for a specific playlist (defaults to 2v2/doubles)."""
    data = await get_player_stats(platform_id, player_id)
    if not data:
        return None

    season_info = data.get('SeasonInfo')
    if not season_info:
        logger.error(f"No season info available for player {player_id}")
        return None

    season_id = str(season_info.get('SeasonID'))
    if not season_id:
        logger.error(f"No season ID available for player {player_id}")
        return None

    ranked_seasons = data.get('RankedSeasons', {})
    season_data = ranked_seasons.get(season_id)
    if not season_data:
        logger.error(f"No season data found for player {player_id} in season {season_id}")
        return None

    playlist_data = season_data.get(playlist_id)
    if not playlist_data:
        logger.error(f"No playlist data found for player {player_id} in playlist {playlist_id}")
        return None

    return playlist_data.get('SkillRating')


def load_mmr_history():
    """Load MMR history from JSON file. Pads with 1388 if needed."""
    history_file = Path(MMR_HISTORY_FILE)
    mmr_history = []
    last_mmr = None
    
    if history_file.exists():
        try:
            with open(history_file, 'r') as f:
                data = json.load(f)
                mmr_history = data.get('mmr_history', [])
                last_mmr = data.get('last_mmr', None)
        except Exception as e:
            logger.error(f"Error loading MMR history: {e}")
    
    # Pad history with 1388 entries if we have fewer than 10 entries
    if len(mmr_history) < 10:
        # Generate timestamps going back in time
        base_time = datetime.now()
        needed = 10 - len(mmr_history)
        
        # Create padding entries with timestamps going back
        for i in range(needed):
            timestamp = (base_time - timedelta(hours=10-i)).isoformat()
            mmr_history.insert(0, {
                'mmr': 1388,
                'timestamp': timestamp
            })
        
        logger.info(f"Padded MMR history with {needed} entries of 1388")
    
    return mmr_history, last_mmr


def save_mmr_history(mmr_history, last_mmr):
    """Save MMR history to JSON file."""
    history_file = Path(MMR_HISTORY_FILE)
    history_file.parent.mkdir(parents=True, exist_ok=True)
    
    data = {
        'mmr_history': mmr_history,
        'last_mmr': last_mmr,
        'last_updated': datetime.now().isoformat()
    }
    
    try:
        with open(history_file, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved MMR history: {len(mmr_history)} entries")
    except Exception as e:
        logger.error(f"Error saving MMR history: {e}")


def update_mmr_history(mmr_history, new_mmr, max_entries=10):
    """Add new MMR to history, keeping only the last max_entries."""
    timestamp = datetime.now().isoformat()
    mmr_history.append({
        'mmr': new_mmr,
        'timestamp': timestamp
    })
    
    # Keep only the last max_entries
    if len(mmr_history) > max_entries:
        mmr_history = mmr_history[-max_entries:]
    
    return mmr_history


async def send_to_overlay(ws, current_mmr, mmr_history):
    """Send MMR data to the overlay via WebSocket."""
    if ws is None:
        logger.debug("WebSocket not connected. Message not sent.")
        return False
    
    try:
        # Check if connection is still open
        if ws.state != websockets.State.OPEN:
            logger.debug("WebSocket not in OPEN state. Message not sent.")
            return False
        
        payload = {
            "type": "rlrank_update",
            "current_mmr": current_mmr,
            "mmr_history": mmr_history
        }
        
        await ws.send(json.dumps(payload))
        logger.info(f"Sent MMR update to overlay: {current_mmr} MMR, {len(mmr_history)} history entries")
        return True
    except websockets.exceptions.ConnectionClosed:
        logger.debug("WebSocket connection closed while sending. Will reconnect.")
        return False
    except Exception as e:
        logger.error(f"Error sending message to overlay: {e}")
        return False


async def check_and_update_rank(ws):
    """Check current rank and update if it has changed."""
    if not RLSTATS_API_KEY:
        logger.error("RLSTATS_API_KEY not set in environment variables")
        return
    
    if not PLAYER_ID:
        logger.error("RL_RANK_PLAYER_ID not set in environment variables")
        return
    
    platform_id = get_platform_id(PLATFORM)
    if platform_id is None:
        logger.error(f"Invalid platform: {PLATFORM}")
        return
    
    logger.info(f"Checking rank for {PLAYER_ID} on {PLATFORM} (2v2/doubles)...")
    
    # Get current MMR
    current_mmr = await get_player_mmr(platform_id, PLAYER_ID, PLAYLIST_MAP['doubles'])
    
    if current_mmr is None:
        logger.warning("Failed to fetch MMR")
        return
    
    logger.info(f"Current MMR: {current_mmr}")
    
    # Load history
    mmr_history, last_mmr = load_mmr_history()
    
    # Only update if MMR has changed
    if current_mmr != last_mmr:
        logger.info(f"MMR changed from {last_mmr} to {current_mmr}")
        
        # Update history
        mmr_history = update_mmr_history(mmr_history, current_mmr)
        save_mmr_history(mmr_history, current_mmr)
        
        # Send to overlay
        await send_to_overlay(ws, current_mmr, mmr_history)
    else:
        logger.info(f"MMR unchanged: {current_mmr}")
        # Still send current state to overlay (in case it reconnected)
        await send_to_overlay(ws, current_mmr, mmr_history)


async def connect_websocket():
    """Maintain a persistent WebSocket connection."""
    websocket = None
    
    while True:
        # Try to connect if not connected
        if websocket is None or websocket.state != websockets.State.OPEN:
            logger.info("Attempting to connect to WebSocket server...")
            try:
                # Connect without using async with so we can manage the connection
                websocket = await websockets.connect(OVERLAY_WS_URL)
                logger.info("Connected to WebSocket server")
                
                # Send initial state on connection
                mmr_history, last_mmr = load_mmr_history()
                # Send history if we have any (including padded entries)
                if mmr_history:
                    # Use last MMR from history if available, otherwise use 1388 (padded value)
                    display_mmr = last_mmr if last_mmr is not None else mmr_history[-1]['mmr']
                    await send_to_overlay(websocket, display_mmr, mmr_history)
                
                # Perform initial rank check
                await check_and_update_rank(websocket)
                    
            except websockets.exceptions.ConnectionClosed:
                logger.debug("Connection closed by server, will reconnect")
                if websocket:
                    try:
                        await websocket.close()
                    except:
                        pass
                websocket = None
            except Exception as e:
                logger.error(f"WebSocket connection error: {e}")
                if websocket:
                    try:
                        await websocket.close()
                    except:
                        pass
                websocket = None
                await asyncio.sleep(2)  # Wait before reconnecting
                continue
        
        # If connected, check rank periodically
        if websocket and websocket.state == websockets.State.OPEN:
            try:
                await asyncio.sleep(CHECK_INTERVAL)
                await check_and_update_rank(websocket)
            except websockets.exceptions.ConnectionClosed:
                logger.debug("Connection closed during rank check, will reconnect")
                websocket = None
            except Exception as e:
                logger.error(f"Error during rank check: {e}")
                # Check if connection is still valid
                try:
                    if websocket and websocket.state != websockets.State.OPEN:
                        websocket = None
                except:
                    websocket = None
        else:
            # Not connected, wait a bit before trying again
            await asyncio.sleep(2)


async def main():
    """Main function."""
    logger.info("Starting Rocket League Rank Bot...")
    logger.info(f"Platform: {PLATFORM}, Player ID: {PLAYER_ID}")
    logger.info(f"Check interval: {CHECK_INTERVAL} seconds")
    
    await connect_websocket()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown initiated by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)

