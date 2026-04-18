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
import re
import sys
from array import array
from pathlib import Path
from threading import Lock
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
GROK_TTS_WS_URL = "wss://api.x.ai/v1/tts"

# Pipeline modes
PIPELINE_REALTIME_AGENT = "realtime_agent"
PIPELINE_LOCAL_STT_TTS = "local_stt_tts"
SUPPORTED_PIPELINE_MODES = {PIPELINE_REALTIME_AGENT, PIPELINE_LOCAL_STT_TTS}
LOCAL_PIPELINE_LANGUAGE = "en"
REALTIME_BG_MIN_CHARS = 90
REALTIME_BG_MIN_SENTENCE_CHARS = 35

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

LOCAL_STT_TTS_INSTRUCTIONS = """

IMPORTANT STT/TTS MODE INSTRUCTIONS:
This mode speaks with text-to-speech and supports expressive speech tags.
You must write responses in English only.

Speech tags you can use:

Inline tags:
- Pauses: [pause], [long-pause], [hum-tune]
- Laughter and crying: [laugh], [chuckle], [giggle], [cry]
- Mouth sounds: [tsk], [tongue-click], [lip-smack]
- Breathing: [breath], [inhale], [exhale], [sigh]

Wrapping tags:
- Volume and intensity: <soft>, <whisper>, <loud>, <build-intensity>, <decrease-intensity>
- Pitch and speed: <higher-pitch>, <lower-pitch>, <slow>, <fast>
- Vocal style: <sing-song>, <singing>, <laugh-speak>, <emphasis>

Examples:
"So I walked in and [pause] there it was. [laugh] I honestly could not believe it!"
"I need to tell you something. <whisper>It is a secret.</whisper> Pretty cool, right?"

Tips for speech tags:
- Place inline tags where the expression would naturally occur in conversation.
- Combine tags with punctuation, like: "Really? [laugh] That's incredible!"
- Use [pause] or [long-pause] for dramatic timing.
- Wrapping tags work best around complete phrases, like: <whisper>It is a secret.</whisper>
- You can combine styles, like: <slow><soft>Goodnight, sleep well.</soft></slow>

Best practices for TTS text:
- Use natural punctuation. Commas, periods, and question marks improve pacing and intonation.
- Add emotional context using exclamation marks and question marks where natural.
- Break longer responses into short paragraphs for natural pauses.

Do not overuse tags. Use them naturally and sparingly so speech still sounds conversational.
"""

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

# Moonshine imports are optional so realtime mode can still run if local deps are missing.
try:
    from moonshine_voice import (
        ModelArch,
        Transcriber,
        TranscriptEventListener,
        get_model_for_language,
    )

    MOONSHINE_AVAILABLE = True
    MOONSHINE_IMPORT_ERROR = ""
except Exception as moonshine_import_error:
    ModelArch = None  # type: ignore[assignment]
    Transcriber = None  # type: ignore[assignment]
    TranscriptEventListener = object  # type: ignore[assignment]
    get_model_for_language = None  # type: ignore[assignment]
    MOONSHINE_AVAILABLE = False
    MOONSHINE_IMPORT_ERROR = str(moonshine_import_error)

MOONSHINE_MODEL_PATH: Optional[str] = None
MOONSHINE_MODEL_ARCH = None
MOONSHINE_MODEL_LOCK = Lock()

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


def ensure_moonshine_medium_streaming_model() -> tuple[str, object]:
    """Ensure the Moonshine English Medium Streaming model exists locally."""
    global MOONSHINE_MODEL_PATH, MOONSHINE_MODEL_ARCH

    if not MOONSHINE_AVAILABLE:
        raise RuntimeError(
            "Moonshine is not available. Install dependencies and verify native runtime setup."
        )

    with MOONSHINE_MODEL_LOCK:
        if MOONSHINE_MODEL_PATH and MOONSHINE_MODEL_ARCH is not None:
            return MOONSHINE_MODEL_PATH, MOONSHINE_MODEL_ARCH

        cache_root_env = os.getenv("MOONSHINE_VOICE_CACHE", "").strip()
        cache_root = Path(cache_root_env).resolve() if cache_root_env else None

        model_path, model_arch = get_model_for_language(
            wanted_language=LOCAL_PIPELINE_LANGUAGE,
            wanted_model_arch=ModelArch.MEDIUM_STREAMING,
            cache_root=cache_root,
        )

        MOONSHINE_MODEL_PATH = model_path
        MOONSHINE_MODEL_ARCH = model_arch

        logger.info("🎙️ Moonshine model ready")
        logger.info(f"   Path: {MOONSHINE_MODEL_PATH}")
        logger.info(f"   Arch: {MOONSHINE_MODEL_ARCH}")

        return MOONSHINE_MODEL_PATH, MOONSHINE_MODEL_ARCH


def build_local_stt_tts_instructions() -> str:
    """Build system instructions for local STT/TTS pipeline."""
    return BASE_INSTRUCTIONS + LOCAL_STT_TTS_INSTRUCTIONS


def pcm16le_bytes_to_float32(audio_bytes: bytes) -> list[float]:
    """Convert PCM16 little-endian bytes to float32 samples in [-1.0, 1.0]."""
    if not audio_bytes:
        return []

    usable_length = len(audio_bytes) - (len(audio_bytes) % 2)
    if usable_length <= 0:
        return []

    pcm_samples = array("h")
    pcm_samples.frombytes(audio_bytes[:usable_length])

    if sys.byteorder != "little":
        pcm_samples.byteswap()

    return [sample / 32768.0 for sample in pcm_samples]


def chunk_text_for_tts(text: str, max_chunk_size: int = 12000) -> list[str]:
    """Chunk text so each TTS text.delta stays under API limits."""
    if not text:
        return []

    if len(text) <= max_chunk_size:
        return [text]

    chunks = []
    current = []
    current_len = 0

    for word in text.split(" "):
        candidate_len = current_len + len(word) + (1 if current else 0)
        if candidate_len > max_chunk_size and current:
            chunks.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len = candidate_len

    if current:
        chunks.append(" ".join(current))

    return chunks


def extract_user_input_text(message: dict) -> str:
    """Extract input_text from conversation.item.create payloads."""
    item = message.get("item", {})
    content = item.get("content", [])
    for part in content:
        if part.get("type") == "input_text":
            return str(part.get("text", "")).strip()
    return ""


# Load settings on startup
load_personality_settings()


@app.on_event("startup")
async def preload_local_stt_model():
    """Warm local STT resources so first local session has minimal setup delay."""
    if not MOONSHINE_AVAILABLE:
        logger.warning(f"⚠️ Moonshine unavailable at startup: {MOONSHINE_IMPORT_ERROR}")
        return

    try:
        await asyncio.to_thread(ensure_moonshine_medium_streaming_model)
    except Exception as e:
        logger.error(f"❌ Failed to initialize Moonshine model at startup: {e}")

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
                    "resolution": "2k",
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
        self.requires_grok_listener = True
        self.grok_ws: Optional[websockets.WebSocketClientProtocol] = None
        self.is_connected = False
        self.is_session_configured = False  # Track if session.update has been sent
        self.tasks: list[asyncio.Task] = []
        self.pending_background_task: Optional[asyncio.Task] = None  # Background generation task
        self.active_response_key: Optional[str] = None
        self.response_transcripts: dict[str, str] = {}
        self.responses_with_queued_background: set[str] = set()

    def _cancel_pending_background_task(self):
        if self.pending_background_task and not self.pending_background_task.done():
            self.pending_background_task.cancel()
        self.pending_background_task = None

    def _extract_response_key(self, data: dict, *, create_fallback: bool = False) -> Optional[str]:
        response = data.get("response")
        if isinstance(response, dict):
            response_id = response.get("id")
            if response_id:
                key = str(response_id)
                self.active_response_key = key
                return key

        response_id = data.get("response_id") or data.get("responseId")
        if response_id:
            key = str(response_id)
            self.active_response_key = key
            return key

        if self.active_response_key:
            return self.active_response_key

        if create_fallback:
            self.active_response_key = "active"
            return self.active_response_key

        return None

    def _mark_response_started(self, data: dict):
        response_key = self._extract_response_key(data, create_fallback=True)
        if not response_key:
            return
        self.response_transcripts[response_key] = ""
        self.responses_with_queued_background.discard(response_key)

    def _should_queue_realtime_background_early(self, transcript: str) -> bool:
        text = transcript.strip()
        if not text:
            return False

        if len(text) >= REALTIME_BG_MIN_CHARS:
            return True

        sentence_match = re.search(r"[.!?。！？](?:\s|$)", text)
        if sentence_match and sentence_match.start() + 1 >= REALTIME_BG_MIN_SENTENCE_CHARS:
            return True

        return False

    def _queue_background_update(self, transcript: str, trigger_source: str):
        text = transcript.strip()
        if not text:
            return

        self._cancel_pending_background_task()
        logger.info(
            f"🖼️ Queueing background update ({trigger_source}) for client #{self.connection_id}"
        )
        self.pending_background_task = asyncio.create_task(
            update_background_if_needed(self.connection_id, text, self.client_ws)
        )

    def _maybe_queue_realtime_background_update(self, data: dict, trigger_source: str):
        response_key = self._extract_response_key(data, create_fallback=True)
        if not response_key:
            return

        transcript = self.response_transcripts.get(response_key, "").strip()
        if not transcript:
            return

        if response_key in self.responses_with_queued_background:
            return

        if not self._should_queue_realtime_background_early(transcript):
            return

        self.responses_with_queued_background.add(response_key)
        self._queue_background_update(transcript, trigger_source)

    def _finalize_realtime_background_update(self, data: dict):
        response_key = self._extract_response_key(data, create_fallback=False)
        if not response_key:
            response_key = self.active_response_key or "active"

        transcript = self.response_transcripts.get(response_key, "").strip()
        if transcript and response_key not in self.responses_with_queued_background:
            self.responses_with_queued_background.add(response_key)
            self._queue_background_update(transcript, "realtime_response_done")

        self.response_transcripts.pop(response_key, None)
        self.responses_with_queued_background.discard(response_key)
        if self.active_response_key == response_key:
            self.active_response_key = None

    def _reset_realtime_response_state(self):
        self.active_response_key = None
        self.response_transcripts.clear()
        self.responses_with_queued_background.clear()
    
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
                        self._cancel_pending_background_task()
                        self._reset_realtime_response_state()

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
                        # Barge-in: cancel any in-progress AI response immediately
                        if self.grok_ws and self.is_connected:
                            try:
                                await self.grok_ws.send(json.dumps({"type": "response.cancel"}))
                                logger.info(f"⏹️ Barge-in: cancelled AI response for client #{self.connection_id}")
                            except Exception as e:
                                logger.error(f"Failed to cancel response on barge-in: {e}")
                        self._cancel_pending_background_task()
                        self._reset_realtime_response_state()
                    elif msg_type == "input_audio_buffer.speech_stopped":
                        logger.info(f"🔇 Speech ended for client #{self.connection_id}")
                    elif msg_type == "error":
                        logger.error(f"❌ Grok error: {json.dumps(data, indent=2)}")
                    elif msg_type == "response.created":
                        self._mark_response_started(data)
                    elif msg_type == "response.audio_transcript.delta":
                        if delta := data.get("delta"):
                            print(delta, end="", flush=True)
                            response_key = self._extract_response_key(data, create_fallback=True)
                            if response_key:
                                self.response_transcripts[response_key] = (
                                    self.response_transcripts.get(response_key, "") + delta
                                )
                            self._maybe_queue_realtime_background_update(
                                data,
                                "realtime_transcript_balanced",
                            )
                    elif msg_type == "response.audio_transcript.done":
                        print()  # Newline after transcript

                        response_key = self._extract_response_key(data, create_fallback=True)
                        transcript_done = str(data.get("transcript", "") or "")
                        if response_key and transcript_done:
                            current_transcript = self.response_transcripts.get(response_key, "")
                            if len(transcript_done) >= len(current_transcript):
                                self.response_transcripts[response_key] = transcript_done

                        self._maybe_queue_realtime_background_update(
                            data,
                            "realtime_transcript_done",
                        )
                        
                        # Translate to English if needed
                        if self.language != 'en' and data.get("transcript"):
                            transcript = data.get("transcript")
                            english_translation = await translate_to_english(transcript, self.language)
                            data["english_translation"] = english_translation
                            logger.info(f"🌐 Translated: {transcript[:30]}... -> {english_translation[:30]}...")
                    elif msg_type == "response.done":
                        logger.info(f"✅ Response complete for client #{self.connection_id}")
                        self._finalize_realtime_background_update(data)
                    
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
        self._cancel_pending_background_task()
        self._reset_realtime_response_state()
        
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


class MoonshineTranscriptListener(TranscriptEventListener):
    """Bridge Moonshine transcript callbacks into async relay events."""

    def __init__(self, relay: "LocalPipelineRelay"):
        self.relay = relay

    def on_line_started(self, event):
        self.relay.on_line_started(event.line)

    def on_line_completed(self, event):
        self.relay.on_line_completed(event.line)

    def on_error(self, event):
        self.relay.on_transcriber_error(event.error)


class LocalPipelineRelay:
    """Local Moonshine STT -> Grok chat -> Grok streaming TTS relay."""

    def __init__(self, client_ws: WebSocket, connection_id: int, language: str = LOCAL_PIPELINE_LANGUAGE):
        self.client_ws = client_ws
        self.connection_id = connection_id
        self.language = LOCAL_PIPELINE_LANGUAGE
        self.requires_grok_listener = False
        self.is_connected = False
        self.closed = False

        self.loop = asyncio.get_running_loop()
        self.transcriber = None
        self.transcript_listener: Optional[MoonshineTranscriptListener] = None

        self.is_user_speaking = False
        self.completed_line_ids: set[int] = set()

        self.active_response_task: Optional[asyncio.Task] = None
        self.pending_background_task: Optional[asyncio.Task] = None
        self.conversation_history: list[dict] = []

    async def connect_to_grok(self):
        """Initialize local STT/TTS pipeline and notify the client."""
        if not XAI_API_KEY:
            await self._safe_send_json({
                "type": "error",
                "error": {"message": "XAI_API_KEY is missing. Set it in .env."},
            })
            return False

        if not MOONSHINE_AVAILABLE:
            await self._safe_send_json({
                "type": "error",
                "error": {
                    "message": "Moonshine dependency is unavailable.",
                    "details": MOONSHINE_IMPORT_ERROR,
                },
            })
            return False

        try:
            model_path, model_arch = await asyncio.to_thread(ensure_moonshine_medium_streaming_model)

            self.transcriber = Transcriber(
                model_path=model_path,
                model_arch=model_arch,
                update_interval=0.25,
            )
            self.transcript_listener = MoonshineTranscriptListener(self)
            self.transcriber.add_listener(self.transcript_listener)
            self.transcriber.start()

            self.is_connected = True
            dynamic_bg_enabled[self.connection_id] = True

            await self._safe_send_json({
                "type": "connection.ready",
                "message": "Connected to local Moonshine STT + Grok streaming TTS",
                "mode": PIPELINE_LOCAL_STT_TTS,
            })

            logger.info(f"✅ Local pipeline ready for client #{self.connection_id}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to initialize local pipeline: {e}")
            await self._safe_send_json({
                "type": "error",
                "error": {
                    "message": "Failed to initialize Moonshine STT pipeline",
                    "details": str(e),
                },
            })
            return False

    async def forward_to_client(self):
        """Local mode does not require a separate upstream listener task."""
        return

    async def forward_to_grok(self, data: bytes | str):
        """Handle client audio/text input for local pipeline mode."""
        if self.closed:
            return

        try:
            if isinstance(data, bytes):
                await self._handle_audio_bytes(data)
            else:
                await self._handle_text_message(data)
        except Exception as e:
            logger.error(f"❌ Local pipeline forward error: {e}")

    async def _handle_audio_bytes(self, audio_bytes: bytes):
        if not self.transcriber or not self.is_connected:
            return

        samples = pcm16le_bytes_to_float32(audio_bytes)
        if not samples:
            return

        self.transcriber.add_audio(samples, 24000)

    async def _handle_text_message(self, payload: str):
        message = json.loads(payload)
        msg_type = message.get("type", "")

        if msg_type == "connect":
            return

        if msg_type == "language.change":
            # Local STT/TTS path is currently English-only.
            requested = message.get("language", LOCAL_PIPELINE_LANGUAGE)
            if requested != LOCAL_PIPELINE_LANGUAGE:
                logger.info(
                    f"🌐 Ignoring non-English local language change request ({requested}) for client #{self.connection_id}"
                )
            self.language = LOCAL_PIPELINE_LANGUAGE
            return

        if msg_type == "dynamic_bg.toggle":
            enabled = message.get("enabled", True)
            dynamic_bg_enabled[self.connection_id] = enabled
            logger.info(
                f"🖼️ Dynamic backgrounds {'enabled' if enabled else 'disabled'} for local client #{self.connection_id}"
            )
            return

        if msg_type == "vision.query":
            await self._handle_vision_query(message)
            return

        if msg_type == "conversation.item.create":
            user_text = extract_user_input_text(message)
            if user_text:
                await self._safe_send_json({
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": user_text,
                })
                self._start_response_task(user_text)
            return

        if msg_type == "response.create":
            # Text responses are started when user input is received.
            return

    async def _handle_vision_query(self, message: dict):
        image_b64 = message.get("image", "")
        query_text = message.get("query", "What do you see?")
        logger.info(f"👁️ Vision query (local pipeline) from client #{self.connection_id}: {query_text}")

        self._cancel_active_response_task(notify_client=True)

        vision_instructions = (
            build_local_stt_tts_instructions()
            + "\n\nThe user is showing you something through their webcam. "
            + "Describe what you see naturally and stay in character. Keep it to 2-3 sentences."
        )

        vision_response = await analyze_vision_query(image_b64, query_text, vision_instructions)

        await self._safe_send_json({
            "type": "vision.response",
            "text": vision_response,
        })

        # Keep the conversation coherent across turns.
        self._start_response_task(
            user_text=f"[Vision query] {query_text}",
            assistant_override=vision_response,
        )

    def on_line_started(self, line):
        if self.closed:
            return

        if not self.is_user_speaking:
            self.is_user_speaking = True
            self._cancel_active_response_task(notify_client=True)
            self._schedule_coroutine(
                self._safe_send_json({"type": "input_audio_buffer.speech_started"})
            )

    def on_line_completed(self, line):
        if self.closed:
            return

        self.is_user_speaking = False
        self._schedule_coroutine(
            self._safe_send_json({"type": "input_audio_buffer.speech_stopped"})
        )

        line_id = getattr(line, "line_id", None)
        if isinstance(line_id, int):
            if line_id in self.completed_line_ids:
                return
            self.completed_line_ids.add(line_id)

        text = str(getattr(line, "text", "") or "").strip()
        if not text:
            return

        self._schedule_coroutine(
            self._safe_send_json({
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": text,
            })
        )
        self._start_response_task(text)

    def on_transcriber_error(self, error: Exception):
        logger.error(f"❌ Moonshine transcription error for client #{self.connection_id}: {error}")
        self._schedule_coroutine(
            self._safe_send_json({
                "type": "error",
                "error": {
                    "message": "Local speech transcription failed",
                    "details": str(error),
                },
            })
        )

    def _schedule_coroutine(self, coro):
        if self.closed:
            return

        def _runner():
            self.loop.create_task(coro)

        self.loop.call_soon_threadsafe(_runner)

    async def _safe_send_json(self, payload: dict):
        if self.closed:
            return
        try:
            await self.client_ws.send_json(payload)
        except Exception:
            # Connection may already be closing.
            pass

    def _cancel_active_response_task(self, notify_client: bool):
        if self.active_response_task and not self.active_response_task.done():
            self.active_response_task.cancel()
            if notify_client:
                self._schedule_coroutine(self._safe_send_json({"type": "response.done"}))
        self._cancel_pending_background_task()
        self.active_response_task = None

    def _start_response_task(self, user_text: str, assistant_override: Optional[str] = None):
        self._cancel_active_response_task(notify_client=False)
        self.active_response_task = self.loop.create_task(
            self._respond_to_user(user_text, assistant_override=assistant_override)
        )

    async def _respond_to_user(self, user_text: str, assistant_override: Optional[str] = None):
        assistant_text = ""
        sent_response_created = False

        try:
            await self._safe_send_json({"type": "response.created"})
            sent_response_created = True

            if assistant_override is not None:
                assistant_text = assistant_override.strip()
            else:
                assistant_text = await self._call_grok_chat(user_text)

            if not assistant_text:
                assistant_text = "Sorry, I could not generate a response right now."

            self._append_history("user", user_text)
            self._append_history("assistant", assistant_text)

            await self._safe_send_json({
                "type": "response.audio_transcript.delta",
                "delta": assistant_text,
            })
            await self._safe_send_json({
                "type": "response.audio_transcript.done",
                "transcript": assistant_text,
            })

            if assistant_text:
                self._queue_background_update(assistant_text, "local_response_start")

            await self._stream_tts_audio(assistant_text)
        except asyncio.CancelledError:
            logger.info(f"⏹️ Cancelled active local response for client #{self.connection_id}")
            raise
        except Exception as e:
            logger.error(f"❌ Local response pipeline error for client #{self.connection_id}: {e}")
            await self._safe_send_json({
                "type": "error",
                "error": {
                    "message": "Local STT/TTS pipeline failed",
                    "details": str(e),
                },
            })
        finally:
            if sent_response_created:
                await self._safe_send_json({"type": "response.done"})

    async def _call_grok_chat(self, user_text: str) -> str:
        messages = [{"role": "system", "content": build_local_stt_tts_instructions()}]
        messages.extend(self.conversation_history)
        messages.append({"role": "user", "content": user_text})

        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                GROK_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {XAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "grok-4-1-fast-non-reasoning",
                    "messages": messages,
                    "temperature": 0.7,
                },
            )

        if response.status_code != 200:
            raise RuntimeError(f"Grok chat failed: {response.status_code} - {response.text}")

        data = response.json()
        return (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )

    async def _stream_tts_audio(self, text: str):
        if not text:
            return

        tts_voice = (VOICE or "eve").lower()
        tts_url = (
            f"{GROK_TTS_WS_URL}?language={LOCAL_PIPELINE_LANGUAGE}"
            f"&voice={tts_voice}&codec=pcm&sample_rate=24000"
        )

        headers = {"Authorization": f"Bearer {XAI_API_KEY}"}

        async with websockets.connect(
            tts_url,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=20,
        ) as tts_ws:
            for chunk in chunk_text_for_tts(text):
                await tts_ws.send(json.dumps({"type": "text.delta", "delta": chunk}))

            await tts_ws.send(json.dumps({"type": "text.done"}))

            async for raw_message in tts_ws:
                event = json.loads(raw_message)
                event_type = event.get("type")

                if event_type == "audio.delta":
                    delta = event.get("delta", "")
                    if delta:
                        await self._safe_send_json({
                            "type": "response.audio.delta",
                            "delta": delta,
                        })
                elif event_type == "audio.done":
                    break
                elif event_type == "error":
                    raise RuntimeError(event.get("message", "Unknown TTS error"))

    def _append_history(self, role: str, content: str):
        self.conversation_history.append({"role": role, "content": content})
        # Keep context bounded to reduce latency and cost.
        if len(self.conversation_history) > 16:
            self.conversation_history = self.conversation_history[-16:]

    def _cancel_pending_background_task(self):
        if self.pending_background_task and not self.pending_background_task.done():
            self.pending_background_task.cancel()
        self.pending_background_task = None

    def _queue_background_update(self, transcript: str, trigger_source: str):
        text = transcript.strip()
        if not text:
            return

        self._cancel_pending_background_task()
        logger.info(
            f"🖼️ Queueing background update ({trigger_source}) for local client #{self.connection_id}"
        )

        self.pending_background_task = asyncio.create_task(
            update_background_if_needed(self.connection_id, text, self.client_ws)
        )

    async def close(self):
        self.closed = True
        self.is_connected = False

        self._cancel_pending_background_task()

        if self.active_response_task and not self.active_response_task.done():
            self.active_response_task.cancel()
            try:
                await self.active_response_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        if self.transcriber:
            try:
                self.transcriber.stop()
            except Exception:
                pass

            try:
                self.transcriber.close()
            except Exception:
                pass

            self.transcriber = None

        if self.connection_id in conversation_topics:
            del conversation_topics[self.connection_id]
        if self.connection_id in dynamic_bg_enabled:
            del dynamic_bg_enabled[self.connection_id]


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    language: str = Query(default='en'),
    mode: str = Query(default=PIPELINE_REALTIME_AGENT),
):
    """WebSocket endpoint for client connections"""
    global connection_count

    # Validate pipeline mode.
    if mode not in SUPPORTED_PIPELINE_MODES:
        logger.warning(f"⚠️ Unsupported pipeline mode '{mode}', falling back to realtime")
        mode = PIPELINE_REALTIME_AGENT
    
    # Validate language
    if language not in LANGUAGE_CONFIG:
        language = 'en'

    # Local STT/TTS path is English-only for now.
    if mode == PIPELINE_LOCAL_STT_TTS:
        language = LOCAL_PIPELINE_LANGUAGE
    
    await websocket.accept()
    connection_count += 1
    connection_id = connection_count
    logger.info(
        f"\n🔌 Client #{connection_id} connected "
        f"(Language: {LANGUAGE_CONFIG[language]['name']}, Mode: {mode})"
    )

    if mode == PIPELINE_LOCAL_STT_TTS:
        relay = LocalPipelineRelay(websocket, connection_id, language)
    else:
        relay = GrokRelay(websocket, connection_id, language)
    
    try:
        # Connect to Grok
        if not await relay.connect_to_grok():
            await websocket.close()
            return
        
        # Start listening to Grok in background for realtime mode only.
        if relay.requires_grok_listener:
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
        "voice": VOICE,
        "moonshine_available": MOONSHINE_AVAILABLE,
        "moonshine_model_ready": bool(MOONSHINE_MODEL_PATH),
    })


@app.get("/config")
async def get_config():
    return JSONResponse({
        "voice": VOICE,
        "wsUrl": f"ws://localhost:{PORT}/ws",
        "languages": {code: config['name'] for code, config in LANGUAGE_CONFIG.items()},
        "pipelines": {
            PIPELINE_REALTIME_AGENT: "Grok Voice Agent",
            PIPELINE_LOCAL_STT_TTS: "Moonshine STT + Grok TTS",
        },
        "defaultPipeline": PIPELINE_REALTIME_AGENT,
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