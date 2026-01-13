from baseBot import BaseBot
from dotenv import load_dotenv
import logging
import asyncio

logger = logging.getLogger(__name__)

class MarblesOnlyBot(BaseBot):
    def __init__(self, stream_name: str = "Feer", overlay_ws_url: str = "ws://localhost:6790"):
        super().__init__(
            overlay_ws_url=overlay_ws_url,
            prefix='!',
            channel_name=stream_name,
            require_client_id=False
        )
        self.stream_name = stream_name
        self.viewer_count_task = None
        self.max_viewers = 0
    
    async def _viewer_count_loop(self):
        """Print viewer count periodically and send to overlay."""
        while True:
            try:
                viewer_count = await self.get_viewer_count(self.stream_name)
                if viewer_count is not None:
                    # Update max viewers if current count is higher
                    if viewer_count > self.max_viewers:
                        self.max_viewers = viewer_count
                    
                    status = "LIVE" if viewer_count > 0 else "OFFLINE"
                    print(f"{self.stream_name}: {viewer_count} viewers ({status}) [Max: {self.max_viewers}]")
                    
                    # Send to overlay
                    import json
                    overlay_data = json.dumps({
                        "type": "viewer_count",
                        "count": viewer_count,
                        "max": self.max_viewers
                    })
                    await self.send_to_overlay(overlay_data)
                else:
                    print(f"{self.stream_name}: Unable to get viewer count")
                await asyncio.sleep(15)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in viewer count loop: {e}")
                await asyncio.sleep(15)
    
    async def event_ready(self):
        """Called when bot is ready."""
        logger.info(f'MarblesOnlyBot logged in as {self.nick}')
        
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
    bot = MarblesOnlyBot(stream_name="Feer")
    bot.run()
