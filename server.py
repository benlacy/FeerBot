import asyncio
import websockets
import logging
from datetime import datetime
from typing import Set, Any

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Store connected clients
connected_clients: Set[Any] = set()

async def handler(websocket: Any) -> None:
    """Handle individual WebSocket connections."""
    client_id = id(websocket)
    remote = websocket.remote_address
    logger.info(f"New client connected! (ID: {client_id}, Remote: {remote})")
    connected_clients.add(websocket)
    
    try:
        async for message in websocket:
            logger.info(f"Received message from client {client_id}: {message}")
            await broadcast(message, sender_id=client_id)
    except websockets.exceptions.ConnectionClosedError as e:
        logger.error(f"Client {client_id} disconnected unexpectedly: {str(e)}")
    except websockets.exceptions.ConnectionClosedOK:
        logger.info(f"Client {client_id} disconnected normally")
    except websockets.exceptions.InvalidMessage as e:
        logger.warning(f"Invalid message from client {client_id}: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error with client {client_id}: {str(e)}", exc_info=True)
    finally:
        if websocket in connected_clients:
            connected_clients.remove(websocket)
            logger.info(f"Client {client_id} removed from connected clients")

async def broadcast(message: str, sender_id: int = None) -> None:
    """Broadcast message to all connected clients except the sender."""
    if not connected_clients:
        logger.warning("No connected clients to broadcast to")
        return

    tasks = []
    for client in connected_clients:
        if id(client) != sender_id:  # Don't send back to the sender
            tasks.append(asyncio.create_task(send_message(client, message)))
    
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(f"Broadcasted message to {len(tasks)} clients")

async def send_message(ws: Any, message: str) -> None:
    """Send a message to a specific WebSocket client."""
    try:
        await ws.send(message)
    except websockets.exceptions.ConnectionClosedOK:
        logger.warning(f"Connection to client {id(ws)} closed. Message not sent.")
    except Exception as e:
        logger.error(f"Error sending message to client {id(ws)}: {str(e)}")

async def main() -> None:
    """Main function to start the WebSocket server."""
    try:
        async with websockets.serve(
            handler,
            "localhost",
            6790,
            ping_interval=20,  # Keep connections alive
            ping_timeout=20,
            close_timeout=10,
            max_size=2**20,  # 1MB max message size
            max_queue=32,    # Max number of messages in queue
            compression=None  # Disable compression for simplicity
        ) as server:
            logger.info("WebSocket server started on ws://localhost:6790")
            logger.info("Waiting for connections...")
            await asyncio.Future()  # run forever
    except Exception as e:
        logger.error(f"Server error: {str(e)}", exc_info=True)
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server shutdown initiated by user")
    except Exception as e:
        logger.error(f"Fatal server error: {str(e)}", exc_info=True)