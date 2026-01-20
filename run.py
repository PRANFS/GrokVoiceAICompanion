"""
Grok Voice AI Companion - Run Script

Simple script to start the FastAPI server.
"""

import os
import sys

# Add app directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

if __name__ == "__main__":
    import uvicorn
    from dotenv import load_dotenv
    
    # Load environment variables
    load_dotenv()
    
    port = int(os.getenv("PORT", 8080))
    
    print("\n" + "=" * 50)
    print("🚀 Grok Voice AI Companion")
    print("=" * 50)
    print(f"📍 Open in browser: http://localhost:{port}")
    print(f"🔌 WebSocket: ws://localhost:{port}/ws")
    print("=" * 50)
    print("\nPress Ctrl+C to stop the server\n")
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        log_level="info"
    )
