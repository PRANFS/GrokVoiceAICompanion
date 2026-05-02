# Grok Voice AI Companion

AI Companion powered by xAI's Grok Voice Agent API with customizable Live2D avatars.

![License](https://img.shields.io/badge/license-MIT-blue.svg)

![](https://raw.githubusercontent.com/PRANFS/GrokVoiceAICompanion/main/static/images/GrokVoice_Imagine.png)

## Features

- **Real-time Voice Conversation** - Talk naturally with Grok AI using WebRTC
- **Dual Voice Pipelines** - Switch between Grok Voice Agent and STT/TTS mode from the top-left toggle
- **Natural Animations** - Eye blinks, breathing, head movements & lip sync (WIP)
- **Dynamic Backgrounds** - AI-generated backgrounds that change based on conversation topics
- **Vision Capabilities** - AI Companion can see your webcam when you ask it to, and respond to visual cues
- **Multi-Language Support** - Supports 24 languages including Japanese, Korean, Chinese, Spanish, French, German, Arabic, Hindi, and more
- **STT/TTS Mode (English-only)** - Moonshine English Medium Streaming STT -> Grok 4.1 Fast Non-Reasoning -> Grok Streaming TTS
- **Customizable Models** - Load your own Live2D models
- **Voice Selection** - Choose from 89 AI voices created by xAI, across 28 languages, including 5 multilingual voices
- **Personality Customization** - Modify AI personality and behavior via API
- **Live Transcripts** - See what you and the AI are saying in real-time
- **Translation Support** - Automatic translation for multi-language conversations

## Project Structure

```
GrokVoiceAICompanion/
├── app/
│   └── main.py              # FastAPI server with WebSocket proxy
├── static/
│   ├── index.html           # Main HTML page
│   ├── voices.json          # Voice registry (89 voices, 24 languages)
│   ├── css/
│   │   └── style.css        # Styles
│   ├── js/
│   │   ├── app.js           # Main application logic
│   │   ├── live2d-avatar.js # Live2D controller
│   │   └── websocket-client.js  # WebSocket + audio handling
│   └── models/              # Live2D model files
│       └── kei_en/          # Sample Kei model
├── models/                  # Original Live2D models
├── .env                     # API key configuration
├── requirements.txt         # Python dependencies
├── run.py                   # Start script
└── venv/                    # Python virtual environment
```

## Prerequisites

- Python 3.9 or higher
- Grok API key from [x.ai](https://x.ai/api)
- Modern browser with microphone support

## Quick Start

### 1. Setup Virtual Environment

```bash
# Create virtual environment (if not already done)
python -m venv venv

# Activate it
# Windows:
venv\Scripts\activate

# macOS/Linux:
source venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure API Key

> **Note:** API Pricing is **$0.05 per minute connected** for Grok Voice Agent and **$0.02 per image generated** for Grok Imagine.  
> 
> **STT/TTS Mode (English-only)** – Moonshine English Medium Streaming STT (free) → Grok 4.1 Fast Non-Reasoning → Grok Streaming TTS  
> This cheaper alternative pipeline typically costs **$0.005 – $0.015 per minute** of conversation (often 5–10x cheaper than the full Voice Agent).  
> Breakdown:  
> - Moonshine STT: **$0** (open-source)  
> - Grok 4.1 Fast Non-Reasoning: **$0.20 / M input tokens** + **$0.50 / M output tokens**  
> - Grok TTS: **$4.20 / M characters** (charged only for generated output)
Create a `.env` file in the project root:

```env
XAI_API_KEY=your_grok_api_key_here
PORT=8080
```

**Get your API key from [x.ai/api](https://x.ai/api)**

### 4. Run the Server

```bash
python run.py
```

> First startup with STT/TTS mode may take longer while the backend auto-downloads Moonshine's English Medium Streaming model.

### 5. Open in Browser

Navigate to: **http://localhost:8080**

Click the microphone button 🎙️ to start talking!

## Usage

1. **Start a conversation** - Click the microphone button or press Space
2. **Talk** - Speak naturally, the AI will respond with voice
3. **Watch the avatar** - Lips sync to the AI's speech
4. **Load custom model** - Click "Load Custom" to browse for your own Live2D models
5. **Change voice & edit personality** - Click "Edit Personality" to choose a language, filter voices by gender, select from 89 voices, and customize the AI's personality
6. **Toggle dynamic background** - Click the dynamic background toggle to turn on/off AI-generated backgrounds that change based on conversation topics

### Voice Pipeline Modes

- **Grok Voice Agent (default)**:
   - Realtime voice-to-voice mode.
   - Language and voice configured via the Edit Personality modal.

- **STT/TTS Mode (English-only)**:
   - Uses Moonshine English Medium Streaming STT locally.
   - Sends transcript text to `grok-4-1-fast-non-reasoning`.
   - Speaks reply through Grok Streaming TTS.
   - Starts listening automatically (no dedicated on/off button).

## Adding Custom Live2D Models (Lipsync may not work properly on custom models)

1. Place your model folder in `static/models/`
2. Add an option to the dropdown in `static/index.html`:
   ```html
   <option value="/static/models/your_model/your_model.model3.json">Your Model</option>
   ```
3. Or use the "Load Custom" button to browse for `.model3.json` files

## License

MIT
