import asyncio
import os
import ssl
import websockets
import logging
from typing import Set, Any, Optional

# Bind address: use 0.0.0.0 on a VPS so remote clients can connect; 127.0.0.1 for local-only.
WS_BIND_HOST = os.getenv("WS_BIND_HOST", "127.0.0.1")
WS_PORT = int(os.getenv("WS_PORT", "6790"))
# Optional TLS (wss://) — set both to PEM paths, e.g. Let's Encrypt fullchain.pem + privkey.pem
WS_SSL_CERTFILE = os.getenv("WS_SSL_CERTFILE", "").strip() or None
WS_SSL_KEYFILE = os.getenv("WS_SSL_KEYFILE", "").strip() or None

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
            # logger.info(f"Received message from client {client_id}: {message}")
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

def _server_ssl_context() -> Optional[ssl.SSLContext]:
    if not WS_SSL_CERTFILE or not WS_SSL_KEYFILE:
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(WS_SSL_CERTFILE, WS_SSL_KEYFILE)
    return ctx


async def main() -> None:
    """Main function to start the WebSocket server."""
    try:
        ssl_ctx = _server_ssl_context()
        scheme = "wss" if ssl_ctx else "ws"
        async with websockets.serve(
            handler,
            WS_BIND_HOST,
            WS_PORT,
            ssl=ssl_ctx,
            ping_interval=20,  # Keep connections alive
            ping_timeout=20,
            close_timeout=10,
            max_size=10 * 2**20,  # 10MB max message size (for base64 audio data)
            max_queue=32,    # Max number of messages in queue
            compression=None  # Disable compression for simplicity
        ) as server:
            logger.info(
                f"WebSocket server started on {scheme}://{WS_BIND_HOST}:{WS_PORT}"
            )
            if ssl_ctx:
                logger.info("TLS enabled (WS_SSL_CERTFILE / WS_SSL_KEYFILE)")
            else:
                logger.info(
                    "Plain WebSocket (set WS_SSL_CERTFILE + WS_SSL_KEYFILE for wss, "
                    "or terminate TLS with nginx/Caddy in front)"
                )
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