# Grok Voice AI Companion

AI Voice Companion powered by Grok Voice Agent API with customizable Live2D avatars.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.9+-blue.svg)

## Features

- 🎤 **Real-time Voice Conversation** - Talk naturally with Grok AI
- 🎭 **Live2D Avatar** - Animated anime-style avatar with lip sync
- 👀 **Natural Animations** - Eye blinks, breathing, head movements
- 🔄 **Customizable Models** - Load your own Live2D models
- 💬 **Live Transcripts** - See what you and the AI are saying

## Project Structure

```
GrokVoiceAICompanion/
├── app/
│   └── main.py              # FastAPI server with WebSocket proxy
├── static/
│   ├── index.html           # Main HTML page
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
- Grok API key from [x.ai](https://x.ai)
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

Edit `.env` in the project root:

```env
XAI_API_KEY=your_grok_api_key_here
PORT=8080
VOICE=eve
INSTRUCTIONS=You are a helpful anime companion. Speak casually and energetically.
```

### 4. Run the Server

```bash
python run.py
```

### 5. Open in Browser

Navigate to: **http://localhost:8080**

Click the microphone button 🎙️ to start talking!

## Usage

1. **Start a conversation** - Click the microphone button or press Space
2. **Talk** - Speak naturally, the AI will respond with voice
3. **Watch the avatar** - Lips sync to the AI's speech, eyes blink, head moves
4. **Change avatar** - Use the dropdown to switch models
5. **Load custom model** - Click "Load Custom" to browse for your own Live2D models

## Configuration

Edit `.env` to customize:

| Variable | Description | Default |
|----------|-------------|---------|
| `XAI_API_KEY` | Your Grok API key | (required) |
| `PORT` | Server port | 8080 |
| `VOICE` | AI voice | eve |
| `INSTRUCTIONS` | System prompt for AI personality | Anime companion |

### Available Voices

- alloy, ash, ballad, coral, echo, fable, onyx, nova, sage, shimmer, verse, eve

## Adding Custom Live2D Models

1. Place your model folder in `static/models/`
2. Add an option to the dropdown in `static/index.html`:
   ```html
   <option value="/static/models/your_model/your_model.model3.json">Your Model</option>
   ```
3. Or use the "Load Custom" button to browse for `.model3.json` files

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| Space | Toggle microphone on/off |

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Main HTML page |
| `GET /health` | Health check |
| `GET /config` | Get public configuration |
| `WS /ws` | WebSocket for voice streaming |

## Troubleshooting

### "Failed to connect" error
- Make sure the server is running (`python run.py`)
- Check that your API key is correct in `.env`

### No audio / microphone not working
- Allow microphone access in your browser
- Check browser console for errors
- Try using Chrome or Edge (best WebSocket/Audio API support)

### Avatar not loading
- Check browser console for 404 errors
- Verify model files exist in `static/models/`

## License

MIT
