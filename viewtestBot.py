from baseBot import BaseBot
from dotenv import load_dotenv
import logging
import asyncio
import aiohttp

logger = logging.getLogger(__name__)

class ViewTestBot(BaseBot):
    def __init__(self, stream_name: str = "Feer", youtube_channel_id: str = "Feer", overlay_ws_url: str = "ws://localhost:6790"):
        super().__init__(
            overlay_ws_url=overlay_ws_url,
            prefix='!',
            channel_name=stream_name,
            require_client_id=False
        )
        self.stream_name = stream_name
        self.youtube_channel_id = youtube_channel_id
        self.viewer_count_task = None
    
    async def _get_youtube_channel_id_from_handle(self, handle: str):
        """
        Convert YouTube handle (e.g., @Feer) to channel ID.
        Handles can be passed with or without @ prefix.
        """
        if not self.youtube_api_key:
            logger.warning("YOUTUBE_API_KEY not set. Cannot resolve channel ID.")
            return None
        
        # Remove @ if present
        handle = handle.lstrip('@')
        
        try:
            # Use channels.list with forHandle parameter (YouTube Data API v3)
            url = f"https://www.googleapis.com/youtube/v3/channels"
            params = {
                "part": "id",
                "forHandle": handle,
                "key": self.youtube_api_key
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        items = data.get("items", [])
                        if items:
                            channel_id = items[0].get("id")
                            logger.info(f"Resolved YouTube handle @{handle} to channel ID: {channel_id}")
                            return channel_id
                        else:
                            logger.warning(f"No channel found for handle @{handle}")
                            return None
                    else:
                        error_text = await response.text()
                        logger.error(f"YouTube API error resolving handle: Status {response.status}, Error: {error_text}")
                        return None
        except Exception as e:
            logger.error(f"Error resolving YouTube handle: {e}")
            return None
    
    async def _viewer_count_loop(self):
        """Print combined viewer count periodically."""
        # Try to resolve YouTube channel ID if it looks like a handle
        youtube_channel_id = self.youtube_channel_id
        if not youtube_channel_id.startswith("UC") and len(youtube_channel_id) < 20:
            # Likely a handle, try to resolve it
            resolved_id = await self._get_youtube_channel_id_from_handle(youtube_channel_id)
            if resolved_id:
                youtube_channel_id = resolved_id
                self.youtube_channel_id = resolved_id
        
        while True:
            try:
                # Get Twitch viewer count
                twitch_viewers = await self.get_viewer_count(self.stream_name)
                if twitch_viewers is None:
                    logger.warning(f"Twitch viewer count returned None for {self.stream_name}")
                    twitch_viewers = 0
                elif twitch_viewers == 0:
                    logger.debug(f"Twitch viewer count is 0 for {self.stream_name} (stream may be offline)")
                
                # Get YouTube viewer count (uses scraping by default)
                youtube_viewers = await self.get_youtube_viewer_count(channel_id=youtube_channel_id)
                if youtube_viewers is None:
                    logger.warning(f"YouTube viewer count returned None for {youtube_channel_id}")
                    youtube_viewers = 0
                elif youtube_viewers == 0:
                    logger.debug(f"YouTube viewer count is 0 for {youtube_channel_id} (stream may be offline)")
                
                # Combine viewer counts
                combined_viewers = (twitch_viewers + youtube_viewers)
                
                # Print to console
                print(f"=== Combined Viewer Count ===")
                print(f"Twitch ({self.stream_name}): {twitch_viewers:,} viewers")
                print(f"YouTube (@{self.youtube_channel_id}): {youtube_viewers:,} viewers")
                print(f"TOTAL: {combined_viewers:,} viewers")
                print("=" * 30)
                
                # Send combined viewer count to overlay
                import json
                overlay_data = json.dumps({
                    "type": "combined_viewer_count",
                    "count": combined_viewers,
                    "twitch": twitch_viewers,
                    "youtube": youtube_viewers
                })
                await self.send_to_overlay(overlay_data)
                
                await asyncio.sleep(15)  # Update every 15 seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in viewer count loop: {e}")
                await asyncio.sleep(15)
    
    async def event_ready(self):
        """Called when bot is ready."""
        logger.info(f'ViewTestBot logged in as {self.nick}')
        
        # Connect to WebSocket if overlay URL is provided
        if self.overlay_ws_url is not None:
            self.websocket_task = asyncio.create_task(self.connect_websocket())
        
        self.viewer_count_task = asyncio.create_task(self._viewer_count_loop())
    
    async def stop(self):
        """Clean up."""
        if self.viewer_count_task:
            self.viewer_count_task.cancel()
            try:
                await self.viewer_count_task
            except asyncio.CancelledError:
                pass

if __name__ == "__main__":
    load_dotenv()
    bot = ViewTestBot(stream_name="Feer", youtube_channel_id="Feer")
    bot.run()
