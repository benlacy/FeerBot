import asyncio
import websockets
from functools import partial

connected_clients = set()

async def handler(websocket):
    print("New client connected!")
    connected_clients.add(websocket)
    try:
        async for message in websocket:
            print(f"Received: {message}")
            await broadcast(message)
    except websockets.exceptions.ConnectionClosedError:
        print("Client disconnected unexpectedly.")
    except websockets.exceptions.ConnectionClosedOK:
        print("Client disconnected normally.")
    except Exception as e:
        print(f"Unexpected error: {e}")
    finally:
        connected_clients.remove(websocket)

async def broadcast(message):
    if connected_clients:  # Send message to all connected clients
        tasks = [asyncio.create_task(send_message(ws, message)) for ws in connected_clients]
        await asyncio.wait(tasks)

async def send_message(ws, message):
    try:
        await ws.send(message)
    except websockets.exceptions.ConnectionClosedOK:
        print(f"Connection to {ws} closed. Ignoring send.")

# Main function to start the WebSocket server
async def main():
    # Start the WebSocket server
    start_server = await websockets.serve(handler, "localhost", 6788)
    print("WebSocket server started on ws://localhost:6788")
    
    # Keep the server running until it is manually closed
    await start_server.wait_closed()

# Run the main function using asyncio
if __name__ == "__main__":
    asyncio.run(main())


#######################################

# start_server = websockets.serve(handler, "localhost", 6789)

# print("WebSocket server started on ws://localhost:6789")
# asyncio.get_event_loop().run_until_complete(start_server)
# asyncio.get_event_loop().run_forever()
