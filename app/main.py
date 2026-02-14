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

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from dotenv import load_dotenv
import websockets
from deep_translator import GoogleTranslator

# Grok Imagine API configuration
GROK_IMAGINE_URL = "https://api.x.ai/v1/images/generations"
GROK_CHAT_URL = "https://api.x.ai/v1/chat/completions"

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
    "\n\nVISION CAPABILITIES: You are connected to the user's webcam and can see them "
    "through it when they ask you to look at something. When the user asks you to "
    "look at something or asks what they are holding, you CAN see them. Never say "
    "you cannot see — you have full vision access. Respond naturally about what you see."
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
SETTINGS_FILE = BASE_DIR / "personality_settings.json"

# Connection tracking
connection_count = 0


def load_personality_settings():
    """Load personality settings from file, or use defaults"""
    global VOICE, BASE_INSTRUCTIONS
    
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                settings = json.load(f)
                VOICE = settings.get('voice', VOICE)
                BASE_INSTRUCTIONS = settings.get('instructions', BASE_INSTRUCTIONS)
                logger.info(f"📂 Loaded personality settings from file")
                logger.info(f"   Voice: {VOICE}")
                logger.info(f"   Instructions: {BASE_INSTRUCTIONS[:50]}...")
        except Exception as e:
            logger.error(f"Failed to load personality settings: {e}")


def save_personality_settings():
    """Save current personality settings to file"""
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                'voice': VOICE,
                'instructions': BASE_INSTRUCTIONS
            }, f, ensure_ascii=False, indent=2)
        logger.info(f"💾 Saved personality settings to file")
    except Exception as e:
        logger.error(f"Failed to save personality settings: {e}")


# Load settings on startup
load_personality_settings()

# Track conversation context for background generation
conversation_topics = {}  # connection_id -> {"current_topic": str, "last_background_url": str}

# Track dynamic background toggle per connection
dynamic_bg_enabled = {}  # connection_id -> bool (default True)


async def analyze_topic_change(connection_id: int, transcript: str) -> tuple[bool, str]:
    """
    Analyze if the conversation topic has changed significantly.
    Returns (has_changed, new_topic_description)
    """
    if not transcript or len(transcript.strip()) < 10:
        return False, ""
    
    current_context = conversation_topics.get(connection_id, {"current_topic": "", "last_background_url": ""})
    current_topic = current_context.get("current_topic", "")
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                GROK_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {XAI_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "grok-4-1-fast-non-reasoning",
                    "messages": [
                        {
                            "role": "system",
                            "content": """You are a topic analyzer. Given the current conversation topic and a new message, determine if the topic/mood has significantly changed.

Respond in JSON format only:
{"changed": true/false, "topic": "brief description of new topic/mood for image generation", "mood": "emotional mood like romantic, happy, sad, exciting, calm, etc."}

The topic should be suitable for generating a background image. Focus on the emotional atmosphere and setting.
Only mark as changed if the conversation shifts to a distinctly different subject or mood.
Keep the topic description under 20 words."""
                        },
                        {
                            "role": "user",
                            "content": f"Current topic: {current_topic if current_topic else 'None (conversation just started)'}\n\nNew message: {transcript}"
                        }
                    ],
                    "temperature": 0.3
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                
                # Parse JSON response
                try:
                    # Extract JSON from response (handle potential markdown formatting)
                    if "```" in content:
                        content = content.split("```")[1]
                        if content.startswith("json"):
                            content = content[4:]
                    
                    analysis = json.loads(content.strip())
                    has_changed = analysis.get("changed", False)
                    new_topic = analysis.get("topic", "")
                    mood = analysis.get("mood", "calm")
                    
                    if has_changed and new_topic:
                        full_topic = f"{mood} atmosphere, {new_topic}"
                        logger.info(f"🎨 Topic changed for client #{connection_id}: {full_topic}")
                        return True, full_topic
                    
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse topic analysis: {e}")
                    
    except Exception as e:
        logger.error(f"Error analyzing topic change: {e}")
    
    return False, ""


async def generate_background_image(topic: str) -> Optional[str]:
    """
    Generate a background image using Grok Imagine API based on the topic.
    Returns the image URL or None if generation fails.
    """
    if not topic:
        return None
    
    try:
        # Create a prompt optimized for background images
        prompt = f"""A beautiful, immersive anime-style background scene. {topic}.
Wide landscape format, dreamy and atmospheric, suitable as a backdrop.
No people or characters, focus on environment and mood.
High quality, detailed, vibrant colors, cinematic lighting."""
        
        logger.info(f"🖼️ Generating background image: {prompt[:80]}...")
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                GROK_IMAGINE_URL,
                headers={
                    "Authorization": f"Bearer {XAI_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "grok-imagine-image",
                    "prompt": prompt,
                    "n": 1,
                    "aspect_ratio": "16:9",
                    "resolution": "1k",
                    "response_format": "url"
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                image_url = result.get("data", [{}])[0].get("url")
                if image_url:
                    logger.info(f"✅ Background image generated: {image_url[:80]}...")
                    return image_url
            else:
                logger.error(f"❌ Image generation failed: {response.status_code} - {response.text}")
                
    except Exception as e:
        logger.error(f"Error generating background image: {e}")
    
    return None


async def analyze_vision_query(image_base64: str, query: str, instructions: str = "") -> str:
    """
    Send an image + text query to grok-4-1-fast-non-reasoning via /v1/chat/completions.
    Returns the text response.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            messages = []
            if instructions:
                messages.append({"role": "system", "content": instructions})
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_base64}",
                            "detail": "auto"
                        }
                    },
                    {
                        "type": "text",
                        "text": query
                    }
                ]
            })

            response = await client.post(
                GROK_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {XAI_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "grok-4-1-fast-non-reasoning",
                    "messages": messages,
                    "temperature": 0.7
                }
            )

            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                logger.info(f"👁️ Vision response: {content[:100]}...")
                return content
            else:
                logger.error(f"❌ Vision API error: {response.status_code} - {response.text}")
                return "Sorry, I couldn't analyze the image right now."

    except Exception as e:
        logger.error(f"Error in vision query: {e}")
        return "Sorry, something went wrong while looking at the image."


async def update_background_if_needed(connection_id: int, transcript: str, client_ws: WebSocket):
    """
    Check if topic changed and generate new background if needed.
    """
    # Check if dynamic backgrounds are enabled for this connection
    if not dynamic_bg_enabled.get(connection_id, True):
        return

    has_changed, new_topic = await analyze_topic_change(connection_id, transcript)
    
    if has_changed and new_topic:
        # Update the stored topic
        if connection_id not in conversation_topics:
            conversation_topics[connection_id] = {}
        conversation_topics[connection_id]["current_topic"] = new_topic
        
        # Generate new background image
        image_url = await generate_background_image(new_topic)
        
        if image_url:
            conversation_topics[connection_id]["last_background_url"] = image_url
            
            # Send background update to client
            await client_ws.send_json({
                "type": "background.update",
                "image_url": image_url,
                "topic": new_topic
            })
            logger.info(f"📤 Sent background update to client #{connection_id}")


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
        self.accumulated_transcript = ""  # Track AI response for topic analysis
        self.pending_background_task: Optional[asyncio.Task] = None  # Background generation task
    
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
        
        # Format voice name for API (capitalize first letter)
        formatted_voice = VOICE.capitalize()
        logger.info(f"🎤 Using voice: {formatted_voice}")
        
        session_update = {
            "type": "session.update",
            "session": {
                "instructions": instructions,
                "voice": formatted_voice,
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

                # Handle dynamic background toggle
                if message.get("type") == "dynamic_bg.toggle":
                    enabled = message.get("enabled", True)
                    dynamic_bg_enabled[self.connection_id] = enabled
                    logger.info(f"🖼️ Dynamic backgrounds {'enabled' if enabled else 'disabled'} for client #{self.connection_id}")
                    return

                # Handle vision query
                if message.get("type") == "vision.query":
                    image_b64 = message.get("image", "")
                    query_text = message.get("query", "What do you see?")
                    logger.info(f"👁️ Vision query from client #{self.connection_id}: {query_text}")

                    # 1) Cancel any in-progress realtime response so the AI
                    #    doesn't blurt out "I can't see you" while we process
                    if self.grok_ws and self.is_connected:
                        await self.grok_ws.send(json.dumps({
                            "type": "response.cancel"
                        }))
                        logger.info(f"⏹️ Cancelled in-progress response for client #{self.connection_id}")

                    # Build system instructions for vision (keep personality)
                    lang_config = LANGUAGE_CONFIG.get(self.language, LANGUAGE_CONFIG['en'])
                    vision_instructions = (
                        BASE_INSTRUCTIONS
                        + lang_config.get('instruction', '')
                        + "\n\nThe user is showing you something via their webcam. "
                        "Describe what you see and respond naturally in character. Keep your answer concise (2-3 sentences)."
                    )

                    # 2) Call the vision side-channel (await the full result)
                    vision_response = await analyze_vision_query(image_b64, query_text, vision_instructions)

                    # Notify the client that vision analysis is done
                    await self.client_ws.send_json({
                        "type": "vision.response",
                        "text": vision_response
                    })

                    # 3) Inject the vision response into the realtime conversation
                    #    so the avatar speaks it aloud
                    if self.grok_ws and self.is_connected:
                        # Add a user context message about what they showed
                        await self.grok_ws.send(json.dumps({
                            "type": "conversation.item.create",
                            "item": {
                                "type": "message",
                                "role": "user",
                                "content": [{
                                    "type": "input_text",
                                    "text": f"[The user showed you an image via webcam and asked: \"{query_text}\"]"
                                }]
                            }
                        }))
                        # Trigger a response with the vision analysis baked in
                        await self.grok_ws.send(json.dumps({
                            "type": "response.create",
                            "response": {
                                "instructions": (
                                    f"The user just showed you something on their webcam. "
                                    f"Based on the image analysis, here is what you see: {vision_response}\n\n"
                                    f"Respond naturally as if you can see it yourself. Stay in character. "
                                    f"Keep it short and conversational (2-3 sentences max)."
                                )
                            }
                        }))
                        logger.info(f"📤 Injected vision response into realtime conversation for client #{self.connection_id}")
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
                    elif msg_type == "error":
                        logger.error(f"❌ Grok error: {json.dumps(data, indent=2)}")
                    elif msg_type == "response.audio_transcript.delta":
                        if delta := data.get("delta"):
                            print(delta, end="", flush=True)
                            self.accumulated_transcript += delta
                    elif msg_type == "response.audio_transcript.done":
                        print()  # Newline after transcript
                        
                        # Translate to English if needed
                        if self.language != 'en' and data.get("transcript"):
                            transcript = data.get("transcript")
                            english_translation = await translate_to_english(transcript, self.language)
                            data["english_translation"] = english_translation
                            logger.info(f"🌐 Translated: {transcript[:30]}... -> {english_translation[:30]}...")
                    elif msg_type == "response.done":
                        logger.info(f"✅ Response complete for client #{self.connection_id}")
                        
                        # Check for topic change and update background (non-blocking)
                        if self.accumulated_transcript:
                            transcript_for_analysis = self.accumulated_transcript
                            self.accumulated_transcript = ""  # Reset for next response
                            
                            # Start background generation task (don't await - let it run in background)
                            if self.pending_background_task:
                                self.pending_background_task.cancel()
                            self.pending_background_task = asyncio.create_task(
                                update_background_if_needed(
                                    self.connection_id,
                                    transcript_for_analysis,
                                    self.client_ws
                                )
                            )
                    
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
        
        # Cancel pending background task
        if self.pending_background_task:
            self.pending_background_task.cancel()
        
        for task in self.tasks:
            task.cancel()
        
        if self.grok_ws:
            await self.grok_ws.close()
            self.grok_ws = None
        
        # Clean up topic tracking for this connection
        if self.connection_id in conversation_topics:
            del conversation_topics[self.connection_id]
        if self.connection_id in dynamic_bg_enabled:
            del dynamic_bg_enabled[self.connection_id]


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


@app.get("/personality")
async def get_personality():
    """Get current personality settings"""
    return JSONResponse({
        "voice": VOICE,
        "instructions": BASE_INSTRUCTIONS
    })


@app.post("/personality")
async def update_personality(request_data: dict):
    """Update personality settings"""
    global VOICE, BASE_INSTRUCTIONS
    
    if "voice" in request_data:
        new_voice = request_data["voice"].lower()
        valid_voices = ["ara", "rex", "sal", "eve", "leo"]
        if new_voice in valid_voices:
            VOICE = new_voice
            logger.info(f"🎤 Voice updated to: {VOICE}")
        else:
            return JSONResponse(
                {"error": f"Invalid voice. Choose from: {', '.join(valid_voices)}"},
                status_code=400
            )
    
    if "instructions" in request_data:
        BASE_INSTRUCTIONS = request_data["instructions"]
        logger.info(f"📝 Instructions updated: {BASE_INSTRUCTIONS[:50]}...")
    
    # Save settings to file for persistence across reloads
    save_personality_settings()
    
    return JSONResponse({
        "success": True,
        "voice": VOICE,
        "instructions": BASE_INSTRUCTIONS
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