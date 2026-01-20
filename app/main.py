"""
Grok Voice AI Companion - FastAPI Backend

This server acts as a relay between the frontend client and the Grok Realtime API.
It handles authentication, message forwarding, and serves the static frontend files.
"""

import os
import json
import asyncio
import base64
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from dotenv import load_dotenv
import websockets

# Load environment variables
load_dotenv()

# Configuration
XAI_API_KEY = os.getenv("XAI_API_KEY")
VOICE = os.getenv("VOICE", "eve")
INSTRUCTIONS = os.getenv("INSTRUCTIONS", "You are a helpful anime companion. Speak casually and energetically.")
PORT = int(os.getenv("PORT", 8080))
GROK_REALTIME_URL = "wss://api.x.ai/v1/realtime"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Validate API key
if not XAI_API_KEY:
    logger.error("❌ XAI_API_KEY not found in .env file")
    logger.error("Please add your Grok API key to the .env file")
else:
    logger.info("🔑 API Key loaded successfully")
    logger.info(f"🎤 Voice set to: {VOICE}")
    logger.info(f"📝 Instructions: {INSTRUCTIONS[:50]}...")

# Create FastAPI app
app = FastAPI(
    title="Grok Voice AI Companion",
    description="AI Voice Companion powered by Grok with Live2D avatars",
    version="1.0.0"
)

# Get paths
BASE_DIR = Path(__file__).parent.parent
STATIC_DIR = BASE_DIR / "static"
MODELS_DIR = BASE_DIR / "models"

# Connection tracking
connection_count = 0


class GrokRelay:
    """Handles the WebSocket relay between client and Grok API"""
    
    def __init__(self, client_ws: WebSocket, connection_id: int):
        self.client_ws = client_ws
        self.connection_id = connection_id
        self.grok_ws: Optional[websockets.WebSocketClientProtocol] = None
        self.is_connected = False
        self.tasks: list[asyncio.Task] = []
    
    async def connect_to_grok(self):
        """Establish connection to Grok Realtime API"""
        logger.info(f"📡 Connecting to Grok API for client #{self.connection_id}...")
        
        # Build WebSocket URL with API key as query parameter
        # Format: wss://api.x.ai/v1/realtime?model=grok-2-latest
        ws_url = f"{GROK_REALTIME_URL}?model=grok-2-latest"
        
        # Headers for WebSocket connection
        headers = {
            "Authorization": f"Bearer {XAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
        
        try:
            self.grok_ws = await websockets.connect(
                ws_url,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=20
            )
            self.is_connected = True
            logger.info(f"✅ Connected to Grok API for client #{self.connection_id}")
            
            # Send session configuration
            await self.send_session_update()
            
            # Notify client
            await self.client_ws.send_json({
                "type": "connection.ready",
                "message": "Connected to Grok Realtime API"
            })
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to connect to Grok: {e}")
            await self.client_ws.send_json({
                "type": "error",
                "error": {"message": "Failed to connect to Grok API", "details": str(e)}
            })
            return False
    
    async def send_session_update(self):
        """Send session configuration to Grok"""
        session_update = {
            "type": "session.update",
            "session": {
                "modalities": ["audio", "text"],
                "voice": VOICE,
                "instructions": INSTRUCTIONS,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": "whisper-1"
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.2,
                    "prefix_padding_ms": 500,
                    "silence_duration_ms": 500
                }
            }
        }
        
        await self.grok_ws.send(json.dumps(session_update))
        logger.info(f"📤 Sent session.update to Grok for client #{self.connection_id}")
    
    async def forward_to_grok(self, data: bytes | str):
        """Forward message from client to Grok"""
        if not self.grok_ws or not self.is_connected:
            return
        
        try:
            if isinstance(data, bytes):
                # Binary audio data - wrap in input_audio_buffer.append
                audio_append = {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(data).decode('utf-8')
                }
                await self.grok_ws.send(json.dumps(audio_append))
            else:
                # JSON message
                message = json.loads(data)
                logger.info(f"📥 Received from client #{self.connection_id}: {message.get('type', 'unknown')}")
                
                # Handle special commands
                if message.get("type") == "connect":
                    return  # Already connected
                
                await self.grok_ws.send(data)
                
        except Exception as e:
            logger.error(f"❌ Error forwarding to Grok: {e}")
    
    async def forward_to_client(self):
        """Forward messages from Grok to client"""
        if not self.grok_ws:
            return
        
        try:
            async for message in self.grok_ws:
                if isinstance(message, bytes):
                    # Binary audio - forward directly
                    await self.client_ws.send_bytes(message)
                else:
                    # JSON message
                    data = json.loads(message)
                    msg_type = data.get("type", "")
                    
                    # Log important events
                    if msg_type == "session.created":
                        logger.info(f"📋 Session created for client #{self.connection_id}")
                    elif msg_type == "session.updated":
                        logger.info(f"🔄 Session updated for client #{self.connection_id}")
                    elif msg_type == "input_audio_buffer.speech_started":
                        logger.info(f"🎤 Speech detected for client #{self.connection_id}")
                    elif msg_type == "input_audio_buffer.speech_stopped":
                        logger.info(f"🔇 Speech ended for client #{self.connection_id}")
                    elif msg_type == "response.done":
                        logger.info(f"✅ Response complete for client #{self.connection_id}")
                    elif msg_type == "error":
                        logger.error(f"❌ Grok error: {data.get('error')}")
                    elif msg_type == "response.audio_transcript.delta":
                        # Log transcript deltas inline
                        if delta := data.get("delta"):
                            print(delta, end="", flush=True)
                    elif msg_type == "response.audio_transcript.done":
                        print()  # Newline after transcript
                    
                    # Forward to client
                    await self.client_ws.send_json(data)
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"🔌 Grok connection closed for client #{self.connection_id}")
        except Exception as e:
            logger.error(f"❌ Error in Grok listener: {e}")
        finally:
            self.is_connected = False
    
    async def close(self):
        """Clean up connections"""
        self.is_connected = False
        
        for task in self.tasks:
            task.cancel()
        
        if self.grok_ws:
            await self.grok_ws.close()
            self.grok_ws = None


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for client connections"""
    global connection_count
    
    await websocket.accept()
    connection_count += 1
    connection_id = connection_count
    logger.info(f"\n🔌 Client #{connection_id} connected")
    
    relay = GrokRelay(websocket, connection_id)
    
    try:
        # Connect to Grok
        if not await relay.connect_to_grok():
            await websocket.close()
            return
        
        # Start listening to Grok in background
        grok_listener = asyncio.create_task(relay.forward_to_client())
        relay.tasks.append(grok_listener)
        
        # Handle client messages
        while True:
            try:
                # Try to receive as text first
                data = await websocket.receive()
                
                if "text" in data:
                    await relay.forward_to_grok(data["text"])
                elif "bytes" in data:
                    await relay.forward_to_grok(data["bytes"])
                    
            except WebSocketDisconnect:
                break
                
    except Exception as e:
        logger.error(f"❌ WebSocket error: {e}")
    finally:
        logger.info(f"👋 Client #{connection_id} disconnected")
        await relay.close()


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return JSONResponse({
        "status": "ok",
        "api_key_configured": bool(XAI_API_KEY),
        "voice": VOICE
    })


@app.get("/config")
async def get_config():
    """Get public configuration"""
    return JSONResponse({
        "voice": VOICE,
        "wsUrl": f"ws://localhost:{PORT}/ws"
    })


# Serve index.html for root
@app.get("/")
async def serve_index():
    """Serve the main HTML page"""
    return FileResponse(STATIC_DIR / "index.html")


# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/models", StaticFiles(directory=MODELS_DIR), name="models")


def main():
    """Run the server"""
    import uvicorn
    
    logger.info("\n========================================")
    logger.info("🚀 Grok Voice AI Companion")
    logger.info("========================================")
    logger.info(f"📍 Server: http://localhost:{PORT}")
    logger.info(f"🔌 WebSocket: ws://localhost:{PORT}/ws")
    logger.info(f"💓 Health: http://localhost:{PORT}/health")
    logger.info("========================================\n")
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=PORT,
        reload=True,
        log_level="info"
    )


if __name__ == "__main__":
    main()
