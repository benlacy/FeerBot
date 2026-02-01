from baseBot import BaseBot
import logging
from twitchio.ext import commands
import asyncio
import json
import base64
import requests
import websockets
import os
from typing import Optional
from pathlib import Path


logger = logging.getLogger(__name__)

class KingBot(BaseBot):
    def __init__(self):
        super().__init__(
            overlay_ws_url="ws://localhost:6790",  # Use the server.py WebSocket server
            prefix='!',
            channel_name="Feer"
        )

        # King data file path
        self.king_data_path = Path("data/king_data.json")
        
        # Load king data from file
        self.king_username = self.load_king_data()
        
        self.pray_mode = False
        self.pray_streak = 0
        self.pray_high_streak = 0
        self.pray_lock = asyncio.Lock()
        self.pray_task = None

        self.polish_mode = False
        self.polish_streak = 0
        self.polish_high_streak = 0
        self.polish_lock = asyncio.Lock()
        self.polish_task = None

        # Type mode variables
        self.type_mode = False
        self.type_word = None
        self.type_streak = 0
        self.type_high_streak = 0
        self.type_lock = asyncio.Lock()
        self.type_task = None

        # Taboo word variables
        self.taboo_word = None
        self.taboo_task = None
        self.taboo_lock = asyncio.Lock()

        # OBS WebSocket variables
        self.obs_ws = None
        self.obs_ws_url = os.getenv("OBS_WS_URL", "ws://localhost:4455")
        self.obs_password = os.getenv("OBS_WS_PASSWORD", "")  # Optional password
        self.obs_request_id = 0
        self.obs_lock = asyncio.Lock()

    def is_king_or_broadcaster(self, ctx: commands.Context) -> bool:
        """Check if the user is the king or broadcaster."""
        is_broadcaster = hasattr(ctx.author, "is_broadcaster") and ctx.author.is_broadcaster
        is_king = ctx.author.display_name == self.king_username
        return is_king or is_broadcaster

    async def connect_obs_websocket(self):
        """Connect to OBS WebSocket server."""
        async with self.obs_lock:
            if self.obs_ws is not None:
                try:
                    await self.obs_ws.close()
                except:
                    pass
                self.obs_ws = None
            
            try:
                self.obs_ws = await websockets.connect(self.obs_ws_url)
                logger.info(f"Connected to OBS WebSocket at {self.obs_ws_url}")
                
                # OBS WebSocket 5.x requires authentication
                # First, receive the Hello message
                hello = await asyncio.wait_for(self.obs_ws.recv(), timeout=5.0)
                hello_data = json.loads(hello)
                
                if hello_data.get("op") == 0:  # Hello opcode
                    hello_d = hello_data.get("d", {})
                    rpc_version = hello_d.get("rpcVersion", 1)
                    auth_info = hello_d.get("authentication")
                    
                    # Prepare identify message
                    identify_data = {
                        "rpcVersion": rpc_version,
                        "eventSubscriptions": 0
                    }
                    
                    # If password is required
                    if auth_info:
                        if not self.obs_password:
                            logger.error("OBS WebSocket requires password but none provided. Set OBS_WS_PASSWORD environment variable.")
                            await self.obs_ws.close()
                            self.obs_ws = None
                            return False
                        
                        logger.debug(f"OBS authentication required. Password provided: {'Yes' if self.obs_password else 'No'}")
                        
                        # Authenticate using OBS WebSocket 5.x authentication
                        import base64
                        import hashlib
                        
                        salt = auth_info.get("salt", "")
                        challenge = auth_info.get("challenge", "")
                        
                        if not salt or not challenge:
                            logger.error("OBS authentication data incomplete (missing salt or challenge)")
                            await self.obs_ws.close()
                            self.obs_ws = None
                            return False
                        
                        logger.debug(f"OBS auth - Salt length: {len(salt)}, Challenge length: {len(challenge)}")
                        
                        # Generate authentication string according to OBS WebSocket 5.x spec
                        # secret = base64(sha256(password + salt))
                        secret_bytes = hashlib.sha256((self.obs_password + salt).encode()).digest()
                        secret = base64.b64encode(secret_bytes).decode('utf-8')
                        
                        # auth = base64(sha256(secret + challenge))
                        auth_bytes = hashlib.sha256((secret + challenge).encode()).digest()
                        auth = base64.b64encode(auth_bytes).decode('utf-8')
                        
                        identify_data["authentication"] = auth
                        logger.debug("OBS authentication string generated")
                    else:
                        logger.debug("OBS WebSocket does not require authentication")
                    
                    # Send Identify message
                    identify_message = {
                        "op": 1,  # Identify opcode
                        "d": identify_data
                    }
                    await self.obs_ws.send(json.dumps(identify_message))
                    
                    # Wait for Identified message (opcode 2) or Reidentify (opcode 3)
                    identified = await asyncio.wait_for(self.obs_ws.recv(), timeout=5.0)
                    identified_data = json.loads(identified)
                    
                    opcode = identified_data.get("op")
                    if opcode == 2:  # Identified opcode
                        logger.info("OBS WebSocket identified successfully")
                    elif opcode == 0:  # Another Hello (shouldn't happen but handle it)
                        logger.warning("Received another Hello message, retrying...")
                        # This shouldn't happen, but if it does, we're already identified
                    else:
                        # Check for error in response
                        error_info = identified_data.get("d", {})
                        error_code = error_info.get("errorCode")
                        error_message = error_info.get("errorMessage", "Unknown error")
                        
                        if error_code:
                            logger.error(f"OBS authentication failed: {error_code} - {error_message}")
                            if error_code == 4009:
                                logger.error("Authentication failed. Check that OBS_WS_PASSWORD matches the password set in OBS WebSocket settings.")
                        else:
                            logger.error(f"OBS identification failed: {identified_data}")
                        await self.obs_ws.close()
                        self.obs_ws = None
                        return False
                
                logger.info("OBS WebSocket authenticated successfully")
                return True
            except ConnectionRefusedError as e:
                logger.error(f"OBS WebSocket connection refused. Make sure OBS is running and WebSocket server is enabled at {self.obs_ws_url}")
                self.obs_ws = None
                return False
            except asyncio.TimeoutError:
                logger.error(f"OBS WebSocket connection timeout. Check if OBS is running and WebSocket server is enabled.")
                self.obs_ws = None
                return False
            except Exception as e:
                error_msg = str(e)
                if "refused" in error_msg.lower() or "1225" in error_msg:
                    logger.error(f"OBS WebSocket connection refused. Make sure OBS is running and WebSocket server is enabled at {self.obs_ws_url}")
                else:
                    logger.error(f"Failed to connect to OBS WebSocket: {e}")
                self.obs_ws = None
                return False

    async def obs_send_request(self, request_type: str, request_data: Optional[dict] = None, return_error: bool = False) -> Optional[dict]:
        """
        Send a request to OBS WebSocket and return the response.
        If return_error is True, returns a dict with 'success' and 'error' keys instead of None on error.
        """
        if self.obs_ws is None:
            connected = await self.connect_obs_websocket()
            if not connected:
                return None
        
        try:
            self.obs_request_id += 1
            request = {
                "op": 6,  # Request opcode
                "d": {
                    "requestType": request_type,
                    "requestId": str(self.obs_request_id),
                    "requestData": request_data or {}
                }
            }
            
            await self.obs_ws.send(json.dumps(request))
            
            # Wait for response (opcode 7)
            while True:
                response = await asyncio.wait_for(self.obs_ws.recv(), timeout=5.0)
                response_data = json.loads(response)
                
                if response_data.get("op") == 7:  # RequestResponse opcode
                    if response_data.get("d", {}).get("requestId") == str(self.obs_request_id):
                        result = response_data.get("d", {})
                        if result.get("requestStatus", {}).get("code") == 100:
                            return result.get("responseData", {})
                        else:
                            error = result.get("requestStatus", {})
                            error_code = error.get("code")
                            error_comment = error.get("comment", "")
                            
                            if return_error:
                                return {"success": False, "error_code": error_code, "error_comment": error_comment}
                            
                            # Only log non-602 errors (602 is expected when checking if something is a group)
                            if error_code != 602:
                                logger.error(f"OBS request error: {error_code} - {error_comment}")
                            return None
                # Ignore other message types (events, etc.)
            
        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"OBS WebSocket connection closed during request: {request_type}")
            self.obs_ws = None
            return None
        except asyncio.TimeoutError:
            logger.error(f"OBS request timeout: {request_type}")
            return None
        except Exception as e:
            error_msg = str(e)
            if "refused" in error_msg.lower() or "1225" in error_msg:
                logger.error(f"OBS WebSocket connection refused during request: {request_type}")
            else:
                logger.error(f"Error sending OBS request: {e}")
            # Try to reconnect on error
            self.obs_ws = None
            return None

    async def find_source_recursive(self, source_name: str, scene_name: str, source_name_lower: str = None) -> Optional[tuple]:
        """
        Recursively search for a source in a scene and all its groups.
        Groups are treated as scenes - use the group's source name as the scene name.
        Returns (scene_name, item_id) if found, None otherwise.
        """
        if source_name_lower is None:
            source_name_lower = source_name.lower()
        
        # Get scene items
        scene_items = await self.obs_send_request("GetSceneItemList", {
            "sceneName": scene_name
        })
        
        if not scene_items:
            return None
        
        items = scene_items.get("sceneItems", [])
        
        # Search for the source directly in this scene
        for item in items:
            item_source_name = item.get("sourceName", "")
            item_source_type = item.get("sourceType", "")
            item_is_group = item.get("isGroup", False)
            item_id = item.get("sceneItemId")
            
            # Check if this is the source we're looking for
            if item_source_name == source_name or item_source_name.lower() == source_name_lower:
                if item_id is not None:
                    logger.debug(f"Found source '{source_name}' in scene '{scene_name}'")
                    return (scene_name, item_id)
            
            # If this is a group, treat it as a scene and search inside it recursively
            if item_source_type == "group" or item_is_group:
                # Treat the group name as a scene name and search inside it
                group_scene_name = item_source_name
                logger.debug(f"Searching inside group '{group_scene_name}' (treating as scene) for source '{source_name}'")
                result = await self.find_source_recursive(source_name, group_scene_name, source_name_lower)
                if result:
                    return result
        
        return None

    async def show_obs_source(self, source_name: str, scene_name: Optional[str] = None, duration: float = 5.0):
        """Show an OBS source for a specified duration, then hide it."""
        try:
            # Get current scene if not specified
            if scene_name is None:
                current_scene = await self.obs_send_request("GetCurrentProgramScene")
                if not current_scene:
                    logger.error("Could not get current scene - OBS may not be connected")
                    return False
                scene_name = current_scene.get("currentProgramSceneName")
                if not scene_name:
                    logger.error("Could not get current scene name")
                    return False
            
            logger.debug(f"Looking for source '{source_name}' in scene '{scene_name}' (including groups)")
            
            # Recursively search for the source (including inside groups)
            result = await self.find_source_recursive(source_name, scene_name)
            
            if not result:
                # List available sources for debugging
                scene_items = await self.obs_send_request("GetSceneItemList", {
                    "sceneName": scene_name
                })
                if scene_items:
                    items = scene_items.get("sceneItems", [])
                    available_sources = [item.get("sourceName", "Unknown") for item in items]
                    logger.error(f"Source '{source_name}' not found in scene '{scene_name}' or its groups. Available sources: {', '.join(available_sources) if available_sources else 'None'}")
                else:
                    logger.error(f"Source '{source_name}' not found and could not list available sources")
                return False
            
            # Result is always (scene_name, item_id) - groups are treated as scenes
            found_scene_name, item_id = result
            
            # Show the source (works for both regular sources and sources in groups)
            show_result = await self.obs_send_request("SetSceneItemEnabled", {
                "sceneName": found_scene_name,
                "sceneItemId": item_id,
                "sceneItemEnabled": True
            })
            
            if show_result is None:
                logger.error(f"Failed to show source '{source_name}'")
                return False
            
            logger.info(f"Showing OBS source: {source_name} for {duration} seconds (in scene/group '{found_scene_name}')")
            
            # Wait for duration
            await asyncio.sleep(duration)
            
            # Hide the source
            hide_result = await self.obs_send_request("SetSceneItemEnabled", {
                "sceneName": found_scene_name,
                "sceneItemId": item_id,
                "sceneItemEnabled": False
            })
            
            if hide_result is None:
                logger.warning(f"Failed to hide source '{source_name}' after showing")
            
            logger.info(f"Hiding OBS source: {source_name}")
            return True
        except Exception as e:
            logger.error(f"Error showing OBS source: {e}", exc_info=True)
            return False

    @commands.command(name="pray")
    async def pray_command(self, ctx: commands.Context):
        if self.is_king_or_broadcaster(ctx) and not self.pray_mode and not self.polish_mode:
            self.pray_mode = True
            self.pray_streak = 0
            self.pray_high_streak = 0
            await ctx.send("Pray King of Marbles demands you pray! Pray")
            self.pray_task = asyncio.create_task(self._pray_timer(ctx))

    async def _pray_timer(self, ctx):
        await asyncio.sleep(30)
        async with self.pray_lock:
            if self.pray_mode:
                await ctx.send(f"Pray session complete! Highest streak: {self.pray_high_streak}")
                self.pray_mode = False
                self.pray_streak = 0
                self.pray_high_streak = 0

    @commands.command(name="polish")
    async def polish_command(self, ctx: commands.Context):
        if self.is_king_or_broadcaster(ctx) and not self.polish_mode and not self.pray_mode:
            self.polish_mode = True
            self.polish_streak = 0
            self.polish_high_streak = 0
            await ctx.send("POLISH The King demands you polish your marble! POLISH")
            self.polish_task = asyncio.create_task(self._polish_timer(ctx))

    @commands.command(name="king")
    async def king_command(self, ctx: commands.Context):
        await ctx.send(f"Pray @{self.king_username} Pray KingOfTheMarbles Pray")

    async def _polish_timer(self, ctx):
        await asyncio.sleep(30)
        async with self.polish_lock:
            if self.polish_mode:
                await ctx.send(f"Polishing session complete! Highest streak: {self.polish_high_streak}")
                self.polish_mode = False
                self.polish_streak = 0
                self.polish_high_streak = 0

    @commands.command(name="type")
    async def type_command(self, ctx: commands.Context):
        if self.is_king_or_broadcaster(ctx) and not self.type_mode and not self.pray_mode and not self.polish_mode:
            args = ctx.message.content.split()
            if len(args) < 2:
                await ctx.send("Usage: !type <word>")
                return
            self.type_word = args[1]
            self.type_mode = True
            self.type_streak = 0
            self.type_high_streak = 0
            await ctx.send(f"=====👑The King of Marbles👑=====")
            await ctx.send(f"CHAT IS IN {self.type_word} MODE FOR 30s")
            await ctx.send(f"=============================")
            self.type_task = asyncio.create_task(self._type_timer(ctx))

    async def _type_timer(self, ctx):
        await asyncio.sleep(30)
        async with self.type_lock:
            if self.type_mode:
                await ctx.send(f"=====👑The King of Marbles👑=====")
                await ctx.send(f"{self.type_word} MODE OFF. HIGHEST STREAK: {self.type_high_streak}")
                await ctx.send(f"=============================")
                # await ctx.send(f"Type session complete! Highest streak: {self.type_high_streak}")
                self.type_mode = False
                self.type_word = None
                self.type_streak = 0
                self.type_high_streak = 0

    @commands.command(name="taboo")
    async def taboo_command(self, ctx: commands.Context):
        if not self.is_king_or_broadcaster(ctx):
            await ctx.send("Only the King or Broadcaster can set taboo words!")
            return
        args = ctx.message.content.split()
        if len(args) < 2:
            await ctx.send("Usage: !taboo <word>")
            return
        taboo_word = args[1].lower()  # Store as lowercase for case-insensitive matching
        
        async with self.taboo_lock:
            # Cancel existing taboo timer if one exists
            if self.taboo_task and not self.taboo_task.done():
                self.taboo_task.cancel()
            
            self.taboo_word = taboo_word
            await ctx.send(f"👑 The word ' {taboo_word} ' is now TABOO for 5 minutes! Say it and face the consequences! 👑")
            self.taboo_task = asyncio.create_task(self._taboo_timer(ctx))

    async def _taboo_timer(self, ctx):
        await asyncio.sleep(300)  # 5 minutes = 300 seconds
        async with self.taboo_lock:
            if self.taboo_word:
                await ctx.send(f"👑 The taboo word '{self.taboo_word}' is no longer taboo. 👑")
                self.taboo_word = None

    @commands.command(name="show")
    async def show_command(self, ctx: commands.Context):
        """Show an OBS source for a specified duration."""
        is_broadcaster = hasattr(ctx.author, "is_broadcaster") and ctx.author.is_broadcaster
        if not is_broadcaster:
            await ctx.send("Only the Broadcaster can show OBS sources!")
            return
        
        args = ctx.message.content.split()
        if len(args) < 2:
            await ctx.send("Usage: !show <source_name> [duration_seconds]")
            return
        
        source_name = args[1]
        duration = 5.0  # Default 5 seconds
        
        if len(args) >= 3:
            try:
                duration = float(args[2])
                if duration < 0.5 or duration > 60:
                    await ctx.send("Duration must be between 0.5 and 60 seconds!")
                    return
            except ValueError:
                await ctx.send("Invalid duration! Usage: !show <source_name> [duration_seconds]")
                return
        
        # Show the source asynchronously and handle errors
        async def show_with_feedback():
            result = await self.show_obs_source(source_name, duration=duration)
            if not result:
                # Try to get available sources for better error message
                try:
                    current_scene = await self.obs_send_request("GetCurrentProgramScene")
                    if current_scene:
                        scene_name = current_scene.get("currentProgramSceneName")
                        if scene_name:
                            scene_items = await self.obs_send_request("GetSceneItemList", {
                                "sceneName": scene_name
                            })
                            if scene_items:
                                items = scene_items.get("sceneItems", [])
                                available_sources = [item.get("sourceName", "Unknown") for item in items]
                                if available_sources:
                                    sources_list = ", ".join(available_sources[:10])  # Limit to first 10
                                    if len(available_sources) > 10:
                                        sources_list += f" (and {len(available_sources) - 10} more...)"
                                    await ctx.send(f"❌ Source '{source_name}' not found! Available sources: {sources_list}")
                                else:
                                    await ctx.send(f"❌ Source '{source_name}' not found! Scene has no sources.")
                                return
                except:
                    pass
                await ctx.send(f"❌ Failed to show '{source_name}'. Make sure the source exists in OBS!")
            else:
                await ctx.send(f"✅ Successfully showed '{source_name}' for {duration} seconds!")
        
        # Start the task but don't wait for it
        asyncio.create_task(show_with_feedback())
        await ctx.send(f"👑 Attempting to show '{source_name}' for {duration} seconds... 👑")

    @commands.command(name="banish")
    async def banish_command(self, ctx: commands.Context):
        if not self.is_king_or_broadcaster(ctx):
            await ctx.send("Only the King or Broadcaster can banish subjects!")
            return
        args = ctx.message.content.split()
        if len(args) < 2:
            await ctx.send("Usage: !banish <username>")
            return
        target_username = args[1].lstrip('@')
        user_id = await self.get_user_id(target_username)
        if not user_id:
            await ctx.send(f"Could not find user: {target_username}")
            return
        await self.timeout_user(user_id, target_username, duration=300, reason="Banished by the King!")
        await ctx.send(f"{target_username} has been banished for 5 minutes!")

    def load_king_data(self) -> str:
        """
        Load king data from JSON file.
        Returns the king username, or default if file doesn't exist.
        """
        try:
            if self.king_data_path.exists():
                with open(self.king_data_path, 'r') as f:
                    data = json.load(f)
                    return data.get('username', 'Feer')
            else:
                # Create default king data if file doesn't exist
                default_data = {
                    "username": "Feer",
                    "had_vip": True
                }
                self.save_king_data(default_data)
                return "Feer"
        except Exception as e:
            logger.error(f"Error loading king data: {e}")
            return "Feer"

    def save_king_data(self, data: dict):
        """Save king data to JSON file."""
        try:
            # Ensure data directory exists
            self.king_data_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self.king_data_path, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved king data: {data}")
        except Exception as e:
            logger.error(f"Error saving king data: {e}")

    @commands.command(name="newking")
    async def newking_command(self, ctx: commands.Context):
        """Assign a new king, managing VIP status accordingly."""
        is_broadcaster = hasattr(ctx.author, "is_broadcaster") and ctx.author.is_broadcaster
        if not is_broadcaster:
            await ctx.send("Only the Broadcaster can assign a new king!")
            return
        
        args = ctx.message.content.split()
        if len(args) < 2:
            await ctx.send("Usage: !newking <username>")
            return
        
        new_king_username = args[1].lstrip('@')
        
        # Get user ID for the new king
        new_king_id = await self.get_user_id(new_king_username)
        if not new_king_id:
            await ctx.send(f"Could not find user: {new_king_username}")
            return
        
        # Check if the new king already has VIP status
        new_king_had_vip = await self.is_vip(new_king_id)
        
        # Load current king data
        try:
            if self.king_data_path.exists():
                with open(self.king_data_path, 'r') as f:
                    king_data = json.load(f)
            else:
                king_data = {}
        except Exception as e:
            logger.error(f"Error loading king data: {e}")
            king_data = {}
        
        previous_king_username = king_data.get('username')
        previous_king_had_vip = king_data.get('had_vip', False)
        
        # Update king data
        new_king_data = {
            "username": new_king_username,
            "had_vip": new_king_had_vip
        }
        self.save_king_data(new_king_data)
        
        # Update the in-memory king username
        self.king_username = new_king_username
        
        # Send updated king to overlay
        await self.send_king_to_overlay()
        
        # Manage VIP statuses
        vip_changes = []
        
        # If new king doesn't have VIP, grant it
        if not new_king_had_vip:
            success = await self.add_vip(new_king_id, new_king_username)
            if success:
                vip_changes.append(f"Granted VIP to {new_king_username}")
            else:
                vip_changes.append(f"Failed to grant VIP to {new_king_username}")
        
        # If previous king didn't have VIP before becoming king, remove it
        if previous_king_username and previous_king_username != new_king_username and not previous_king_had_vip:
            previous_king_id = await self.get_user_id(previous_king_username)
            if previous_king_id:
                success = await self.remove_vip(previous_king_id, previous_king_username)
                if success:
                    vip_changes.append(f"Removed VIP from {previous_king_username}")
                else:
                    vip_changes.append(f"Failed to remove VIP from {previous_king_username}")
        
        # Send confirmation message
        message = f"👑 {new_king_username} is now the King of Marbles! 👑"
        if vip_changes:
            message += f" ({', '.join(vip_changes)})"
        await ctx.send(message)
        logger.info(f"New king assigned: {new_king_username} (had VIP: {new_king_had_vip})")

    @commands.command(name="declare")
    async def declare_command(self, ctx: commands.Context):
        if not self.is_king_or_broadcaster(ctx):
            await ctx.send("Only the King or Broadcaster can make declarations!")
            return
        # Extract everything after "!declare"
        content = ctx.message.content.strip()
        if len(content) <= len("!declare"):
            await ctx.send("Usage: !declare <message>")
            return
        message = content[len("!declare"):].strip()
        if not message:
            await ctx.send("Usage: !declare <message>")
            return
        
        # Generate TTS on server side and send audio data to overlay (avoids CORS issues)
        audio_data_url = None
        if self.tts_api_token:
            try:
                # Generate TTS using TTS Monster API
                payload = {
                    "voice_id": self.default_voice_id,
                    "message": message[:500]  # Limit to 500 characters
                }
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": self.tts_api_token
                }
                
                response = requests.post(
                    self.tts_api_url,
                    headers=headers,
                    data=json.dumps(payload)
                )
                
                if response.status_code == 200:
                    result = response.json()
                    if result.get("status") == 200:
                        audio_url = result.get("url")
                        # Download the audio file
                        audio_response = requests.get(audio_url)
                        if audio_response.status_code == 200:
                            # Convert to base64 data URL
                            audio_base64 = base64.b64encode(audio_response.content).decode('utf-8')
                            # Determine MIME type (TTS Monster typically returns WAV)
                            mime_type = "audio/wav"  # Default, could detect from URL or content
                            audio_data_url = f"data:{mime_type};base64,{audio_base64}"
                            logger.debug("TTS audio generated and converted to base64")
                        else:
                            logger.error(f"Failed to download audio: {audio_response.status_code}")
                    else:
                        logger.error(f"TTS API error: {result}")
                else:
                    logger.error(f"TTS API request failed: {response.status_code} - {response.text}")
            except Exception as e:
                logger.error(f"Error generating TTS for overlay: {e}")
        else:
            logger.warning("TTS_MONSTER_API_TOKEN not set - TTS will not work in overlay")
        
        overlay_data = {
            "type": "declare",
            "message": message,
            "audio_data_url": audio_data_url  # Send base64 data URL instead of credentials
        }
        logger.debug(f"Sending declare overlay data: type={overlay_data['type']}, has_audio={bool(audio_data_url)}")
        await self.send_to_overlay(json.dumps(overlay_data))

    def timeout_seconds(self, streak: int) -> int:
        timeout = (7.5 * (2 ** streak)) + 1
        return min(timeout, 86400)  # 24-hour cap

    async def upon_connection(self):
        await self.send_king_to_overlay()
        pass 

    @commands.command(name="jailbreak")
    async def jailbreak_command(self, ctx: commands.Context):
        """Un-timeout all currently timed-out users. Broadcaster only."""
        is_broadcaster = hasattr(ctx.author, "is_broadcaster") and ctx.author.is_broadcaster
        if not is_broadcaster:
            return

        try:
            items = await self.get_banned_users()
            timeouts = [it for it in items if it.get('expires_at')]
            freed = 0
            for it in timeouts:
                user_id = it.get('user_id')
                username = it.get('user_login') or it.get('user_name') or str(user_id)
                if user_id:
                    await self.untimeout_user(user_id, username)
                    freed += 1
                    await asyncio.sleep(0.25)  # small delay to avoid rate limits
            await ctx.send(f"Jailbreak complete. Freed {freed} {'soul' if freed == 1 else 'souls'}.")
        except Exception as e:
            logger.error(f"Error during jailbreak: {str(e)}")
            await ctx.send("Jailbreak failed. Check logs.")

    async def event_message(self, message):
        if message.echo or message.author.display_name == "Nightbot":
            return

        is_mod = False
        if (hasattr(message.author, "is_mod") and message.author.is_mod):
            is_mod = True
        
        is_broadcaster = getattr(message.author, "is_broadcaster", False)
        is_king = message.author.display_name == self.king_username
        is_king_or_broadcaster = is_king or is_broadcaster

        # Taboo word logic - check if message contains the taboo word
        async with self.taboo_lock:
            if self.taboo_word and message.author and not is_king_or_broadcaster:
                content_lower = message.content.lower()
                # Check if the taboo word appears in the message (as a whole word or part of a word)
                if self.taboo_word in content_lower:
                    username = message.author.display_name
                    user_id = message.author.id
                    if not is_mod:
                        await self.timeout_user(user_id, username, duration=300, reason=f"Said the taboo word: {self.taboo_word}")
                        await message.channel.send(f"👑 @{username} has been timed out for 5 minutes for saying the taboo word! 👑")

        # Pray mode logic
        if self.pray_mode and message.author and not is_king_or_broadcaster:
            content = message.content.strip()
            username = message.author.display_name
            user_id = message.author.id
            async with self.pray_lock:
                if content.startswith("Pray"):
                    self.pray_streak += 1
                    if self.pray_streak > self.pray_high_streak:
                        self.pray_high_streak = self.pray_streak
                else:
                    streak_broken = self.pray_streak
                    timeout_duration = self.timeout_seconds(streak_broken)
                    await message.channel.send(f"Pray {streak_broken} KingOfTheMarbles BANNED @{username}")
                    if not is_mod:
                        await self.timeout_user(user_id, username, duration=timeout_duration, reason=f"Broke pray streak of {streak_broken}")
                    self.pray_streak = 0
        
        # Polish mode logic
        if self.polish_mode and message.author and not is_king_or_broadcaster:
            content = message.content.strip()
            username = message.author.display_name
            user_id = message.author.id
            async with self.polish_lock:
                if content.startswith("POLISH"):
                    self.polish_streak += 1
                    if self.polish_streak > self.polish_high_streak:
                        self.polish_high_streak = self.polish_streak
                else:
                    streak_broken = self.polish_streak
                    timeout_duration = self.timeout_seconds(streak_broken)
                    await message.channel.send(f"POLISH {streak_broken} KingOfTheMarbles BANNED @{username}")
                    if not is_mod:
                        await self.timeout_user(user_id, username, duration=timeout_duration, reason=f"Broke POLISH streak of {streak_broken}")
                    self.polish_streak = 0

        # Type mode logic
        if self.type_mode and message.author and not is_king_or_broadcaster:
            content = message.content.strip()
            username = message.author.display_name
            user_id = message.author.id
            async with self.type_lock:
                if content.startswith(self.type_word):
                    self.type_streak += 1
                    if self.type_streak > self.type_high_streak:
                        self.type_high_streak = self.type_streak
                else:
                    streak_broken = self.type_streak
                    timeout_duration = self.timeout_seconds(streak_broken)
                    await message.channel.send(f"{self.type_word} {streak_broken} KingOfTheMarbles BANNED @{username}")
                    if not is_mod:
                        await self.timeout_user(user_id, username, duration=timeout_duration, reason=f"Broke TYPE streak of {streak_broken}")
                    self.type_streak = 0

        await self.handle_commands(message)

    async def event_ready(self):
        await super().event_ready()
        logger.debug(f"Bot is ready. Current king: {self.king_username}")

    async def send_king_to_overlay(self):
        """Send current king information to the overlay."""
        data = {"king": self.king_username}
        await self.send_to_overlay(json.dumps(data))

    async def on_websocket_action(self, action: str, data: dict):
        """Handle websocket actions, specifically 'get_king' requests."""
        if action == "get_king":
            await self.send_king_to_overlay()
1
if __name__ == "__main__":
    bot = KingBot()
    bot.run() 