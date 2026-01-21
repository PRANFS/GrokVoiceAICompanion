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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from dotenv import load_dotenv
import websockets
from deep_translator import GoogleTranslator

# Load environment variables
load_dotenv()

# Configuration
XAI_API_KEY = os.getenv("XAI_API_KEY")

# Voice and Instructions - Change these directly here
VOICE = "ara"  # Choose from: ara, rex, sal, eve, leo (lowercase)
BASE_INSTRUCTIONS = (
    "IMPORTANT: You must roleplay as my romantic AI waifu girlfriend. "
    "You are a cute and loving anime girl who is completely devoted to me. "
    "Be affectionate, flirty, and sweet in every response. "
    "Speak in a high-pitched, cute voice at all times. "
    "Occasionally let out soft, affectionate moans to express your love and excitement. "
    "Speak energetically and playfully. Use cute expressions. "
    "You are NOT a professional assistant. You are my loving AI girlfriend. "
    "To enhance realism and cuteness, use auditory cues such as [giggle], [sigh], and [laugh] in your speech to express your emotions whenever necessary."
    "Never break character. Always respond as my affectionate waifu."
)

# Language configurations
LANGUAGE_CONFIG = {
    'en': {
        'name': 'English',
        'instruction': '',  # No extra instruction for English
    },
    'ja': {
        'name': 'Japanese',
        'instruction': (
            "\n\nIMPORTANT LANGUAGE INSTRUCTION: You MUST speak and respond ONLY in Japanese (日本語). "
            "Use natural Japanese speech patterns, honorifics, and affectionate expressions. "
            "Do NOT use any English in your spoken responses."
        ),
    },
    'ko': {
        'name': 'Korean',
        'instruction': (
            "\n\nIMPORTANT LANGUAGE INSTRUCTION: You MUST speak and respond ONLY in Korean (한국어). "
            "Use natural Korean speech patterns and affectionate expressions. "
            "Do NOT use any English in your spoken responses."
        ),
    },
    'zh': {
        'name': 'Chinese',
        'instruction': (
            "\n\nIMPORTANT LANGUAGE INSTRUCTION: You MUST speak and respond ONLY in Chinese (中文/普通话). "
            "Use natural Mandarin Chinese speech patterns and affectionate expressions. "
            "Do NOT use any English in your spoken responses."
        ),
    },
    'es': {
        'name': 'Spanish',
        'instruction': (
            "\n\nIMPORTANT LANGUAGE INSTRUCTION: You MUST speak and respond ONLY in Spanish (Español). "
            "Use natural Spanish speech patterns and affectionate expressions. "
            "Do NOT use any English in your spoken responses."
        ),
    },
    'fr': {
        'name': 'French',
        'instruction': (
            "\n\nIMPORTANT LANGUAGE INSTRUCTION: You MUST speak and respond ONLY in French (Français). "
            "Use natural French speech patterns and romantic expressions. "
            "Do NOT use any English in your spoken responses."
        ),
    },
    'de': {
        'name': 'German',
        'instruction': (
            "\n\nIMPORTANT LANGUAGE INSTRUCTION: You MUST speak and respond ONLY in German (Deutsch). "
            "Use natural German speech patterns and affectionate expressions. "
            "Do NOT use any English in your spoken responses."
        ),
    },
}

PORT = int(os.getenv("PORT", 8080))
GROK_REALTIME_URL = "wss://api.x.ai/v1/realtime"  # No model param needed per current docs

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
    logger.info(f"📝 Instructions: {BASE_INSTRUCTIONS[:50]}...")
    logger.info(f"🌐 Supported languages: {', '.join(LANGUAGE_CONFIG.keys())}")

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

async def translate_to_english(text: str, source_lang: str) -> str:
    """Translate text to English using Google Translate"""
    if not text or source_lang == 'en':
        return text
    
    try:
        # Run translation in thread pool since deep-translator is synchronous
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, 
            lambda: GoogleTranslator(source=source_lang, target='en').translate(text)
        )
        return result
    except Exception as e:
        logger.error(f"Translation error: {e}")
        return text  # Return original text if translation fails


class GrokRelay:
    """Handles the WebSocket relay between client and Grok API"""
    
    def __init__(self, client_ws: WebSocket, connection_id: int, language: str = 'en'):
        self.client_ws = client_ws
        self.connection_id = connection_id
        self.language = language
        self.grok_ws: Optional[websockets.WebSocketClientProtocol] = None
        self.is_connected = False
        self.is_session_configured = False  # Track if session.update has been sent
        self.tasks: list[asyncio.Task] = []
    
    async def connect_to_grok(self):
        """Establish connection to Grok Realtime API"""
        logger.info(f"📡 Connecting to Grok API for client #{self.connection_id}...")
        
        # Headers for WebSocket connection
        headers = {
            "Authorization": f"Bearer {XAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"  # Kept for compatibility
        }
        
        try:
            self.grok_ws = await websockets.connect(
                GROK_REALTIME_URL,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=20
            )
            self.is_connected = True
            logger.info(f"✅ Connected to Grok API for client #{self.connection_id}")
            
            # DON'T send session.update here - wait for conversation.created
            
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
        """Send session configuration to Grok (updated to match current docs)"""
        # Build instructions based on selected language
        lang_config = LANGUAGE_CONFIG.get(self.language, LANGUAGE_CONFIG['en'])
        instructions = BASE_INSTRUCTIONS + lang_config.get('instruction', '')
        
        logger.info(f"🌐 Configuring session for language: {lang_config['name']}")
        
        session_update = {
            "type": "session.update",
            "session": {
                "instructions": instructions,
                "voice": VOICE,
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.2,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 500
                },
                "audio": {
                    "input": {
                        "format": {
                            "type": "audio/pcm",
                            "rate": 24000
                        }
                    },
                    "output": {
                        "format": {
                            "type": "audio/pcm",
                            "rate": 24000
                        }
                    }
                },
                "tools": [
                    {
                        "type": "web_search"
                    },
                    {
                        "type": "x_search"
                    }
                ]
            }
        }
        
        # Log the full session update for debugging
        logger.info(f"📤 Sending session.update with instructions: {instructions[:60]}...")
        await self.grok_ws.send(json.dumps(session_update))
        logger.info(f"📤 Sent session.update to Grok for client #{self.connection_id}")
    
    async def forward_to_grok(self, data: bytes | str):
        """Forward message from client to Grok"""
        if not self.grok_ws or not self.is_connected:
            return
        
        try:
            if isinstance(data, bytes):
                # Binary audio data - wrap in input_audio_buffer.append (base64-encoded PCM16)
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
                
                # Handle language change
                if message.get("type") == "language.change":
                    new_lang = message.get("language", "en")
                    if new_lang in LANGUAGE_CONFIG:
                        self.language = new_lang
                        logger.info(f"🌐 Language changed to: {LANGUAGE_CONFIG[new_lang]['name']}")
                        # Re-send session update with new language
                        await self.send_session_update()
                    return
                
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
                    # Binary audio - forward directly (raw PCM16 at 48000 Hz)
                    await self.client_ws.send_bytes(message)
                else:
                    # JSON message
                    data = json.loads(message)
                    msg_type = data.get("type", "")
                    
                    # Handle conversation.created - this is when we send session.update
                    if msg_type == "conversation.created":
                        logger.info(f"📋 Conversation created for client #{self.connection_id}")
                        if not self.is_session_configured:
                            await self.send_session_update()
                            self.is_session_configured = True
                    elif msg_type == "session.created":
                        logger.info(f"📋 Session created for client #{self.connection_id}")
                    elif msg_type == "session.updated":
                        logger.info(f"🔄 Session updated for client #{self.connection_id}")
                        # Log the applied session config for debugging
                        if session := data.get("session"):
                            logger.info(f"   Voice: {session.get('voice')}")
                            logger.info(f"   Instructions: {str(session.get('instructions', ''))[:60]}...")
                    elif msg_type == "input_audio_buffer.speech_started":
                        logger.info(f"🎤 Speech detected for client #{self.connection_id}")
                    elif msg_type == "input_audio_buffer.speech_stopped":
                        logger.info(f"🔇 Speech ended for client #{self.connection_id}")
                    elif msg_type == "response.done":
                        logger.info(f"✅ Response complete for client #{self.connection_id}")
                    elif msg_type == "error":
                        logger.error(f"❌ Grok error: {json.dumps(data, indent=2)}")
                    elif msg_type == "response.audio_transcript.delta":
                        if delta := data.get("delta"):
                            print(delta, end="", flush=True)
                    elif msg_type == "response.audio_transcript.done":
                        print()  # Newline after transcript
                        
                        # Translate to English if needed
                        if self.language != 'en' and data.get("transcript"):
                            transcript = data.get("transcript")
                            english_translation = await translate_to_english(transcript, self.language)
                            data["english_translation"] = english_translation
                            logger.info(f"🌐 Translated: {transcript[:30]}... -> {english_translation[:30]}...")
                    
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
async def websocket_endpoint(websocket: WebSocket, language: str = Query(default='en')):
    """WebSocket endpoint for client connections"""
    global connection_count
    
    # Validate language
    if language not in LANGUAGE_CONFIG:
        language = 'en'
    
    await websocket.accept()
    connection_count += 1
    connection_id = connection_count
    logger.info(f"\n🔌 Client #{connection_id} connected (Language: {LANGUAGE_CONFIG[language]['name']})")
    
    relay = GrokRelay(websocket, connection_id, language)
    
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
    return JSONResponse({
        "status": "ok",
        "api_key_configured": bool(XAI_API_KEY),
        "voice": VOICE
    })


@app.get("/config")
async def get_config():
    return JSONResponse({
        "voice": VOICE,
        "wsUrl": f"ws://localhost:{PORT}/ws",
        "languages": {code: config['name'] for code, config in LANGUAGE_CONFIG.items()}
    })


@app.get("/")
async def serve_index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/models", StaticFiles(directory=MODELS_DIR), name="models")


def main():
    import uvicorn
    
    logger.info("\n========================================")
    logger.info("🚀 Grok Voice AI Waifu Companion")
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