/**
 * Grok Voice AI Companion - Main Application
 * 
 * Integrates Live2D avatar with Grok voice API
 */

// Global instances
let avatar = null;
let wsClient = null;
let isSessionActive = false;
let lipSyncValue = 0;

// DOM Elements
const micButton = document.getElementById('mic-button');
const statusDot = document.getElementById('status-dot');
const statusText = document.getElementById('status-text');
const modelDropdown = document.getElementById('model-dropdown');
const loadCustomBtn = document.getElementById('load-custom-btn');
const customModelInput = document.getElementById('custom-model-input');
const transcriptPanel = document.getElementById('transcript-panel');
const transcriptContent = document.getElementById('transcript-content');
const volumeBars = document.querySelectorAll('.volume-bar');

/**
 * Initialize the application
 */
async function init() {
    console.log('🚀 Initializing Grok Voice AI Companion...');
    
    // Initialize Live2D Avatar
    avatar = new Live2DAvatar('live2d-canvas');
    avatar.onLoad = () => {
        console.log('✅ Avatar loaded');
        micButton.disabled = false;
    };
    avatar.onError = (error) => {
        console.error('Avatar error:', error);
        showError('Failed to load avatar');
    };
    
    // Load default model
    await avatar.loadModel(modelDropdown.value);
    
    // Initialize WebSocket client
    wsClient = new GrokWebSocketClient({
        wsUrl: `ws://${window.location.host}/ws`,
        
        onStateChange: (state) => {
            updateStatus(state);
        },
        
        onTranscript: (text, role, isFinal) => {
            if (isFinal) {
                addTranscript(text, role);
            } else {
                updateCurrentTranscript(text);
            }
        },
        
        onSpeakingChange: (speaking, type) => {
            if (type === 'assistant') {
                updateStatus(speaking ? 'speaking' : 'connected');
            }
        },
        
        onVolumeChange: (volume, type) => {
            // Smooth lip sync
            lipSyncValue = lipSyncValue * 0.7 + volume * 0.3;
            avatar.setLipSync(lipSyncValue);
            
            // Update volume bars
            updateVolumeBars(volume);
        },
        
        onError: (err) => {
            console.error('WebSocket error:', err);
            showError(err.error?.message || 'Connection error');
        }
    });
    
    // Setup event listeners
    setupEventListeners();
}

/**
 * Setup event listeners
 */
function setupEventListeners() {
    // Mic button
    micButton.addEventListener('click', toggleConversation);
    
    // Model dropdown
    modelDropdown.addEventListener('change', () => {
        avatar.loadModel(modelDropdown.value);
    });
    
    // Custom model button
    loadCustomBtn.addEventListener('click', () => {
        customModelInput.click();
    });
    
    // Custom model input
    customModelInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) {
            const url = URL.createObjectURL(file);
            avatar.loadModel(url);
        }
    });
    
    // Keyboard shortcut
    document.addEventListener('keydown', (e) => {
        if (e.code === 'Space' && !e.repeat) {
            e.preventDefault();
            toggleConversation();
        }
    });
}

/**
 * Toggle conversation on/off
 */
async function toggleConversation() {
    if (isSessionActive) {
        stopConversation();
    } else {
        await startConversation();
    }
}

/**
 * Start conversation
 */
async function startConversation() {
    try {
        updateStatus('connecting');
        
        // Connect to WebSocket
        await wsClient.connect();
        
        // Start audio capture
        const success = await wsClient.startAudioCapture();
        if (success) {
            isSessionActive = true;
            micButton.classList.remove('inactive');
            micButton.classList.add('active');
            micButton.textContent = '🎤';
            clearTranscripts();
        }
        
    } catch (error) {
        console.error('Failed to start conversation:', error);
        showError('Failed to start. Is the backend running?');
        updateStatus('disconnected');
    }
}

/**
 * Stop conversation
 */
function stopConversation() {
    wsClient.stopAudioCapture();
    wsClient.disconnect();
    
    isSessionActive = false;
    micButton.classList.remove('active');
    micButton.classList.add('inactive');
    micButton.textContent = '🎙️';
    
    updateStatus('disconnected');
    lipSyncValue = 0;
    avatar.setLipSync(0);
}

/**
 * Update status display
 */
function updateStatus(state) {
    statusDot.className = '';
    
    switch (state) {
        case 'disconnected':
            statusDot.classList.add('disconnected');
            statusText.textContent = 'Click microphone to start';
            break;
        case 'connecting':
            statusDot.classList.add('connecting');
            statusText.textContent = 'Connecting...';
            break;
        case 'connected':
            statusDot.classList.add('connected');
            statusText.textContent = isSessionActive ? 'Listening...' : 'Connected';
            break;
        case 'speaking':
            statusDot.classList.add('speaking');
            statusText.textContent = 'AI Speaking...';
            break;
    }
}

/**
 * Update volume bars
 */
function updateVolumeBars(volume) {
    volumeBars.forEach((bar, index) => {
        const threshold = (index + 1) / volumeBars.length;
        if (volume >= threshold * 0.8) {
            bar.classList.add('active');
        } else {
            bar.classList.remove('active');
        }
    });
}

/**
 * Add transcript line
 */
function addTranscript(text, role) {
    transcriptPanel.classList.remove('hidden');
    
    const line = document.createElement('p');
    line.className = `transcript-line ${role}`;
    line.innerHTML = `<strong>${role === 'user' ? 'You:' : 'AI:'}</strong> ${text}`;
    
    transcriptContent.appendChild(line);
    transcriptPanel.scrollTop = transcriptPanel.scrollHeight;
}

/**
 * Update current (streaming) transcript
 */
function updateCurrentTranscript(text) {
    transcriptPanel.classList.remove('hidden');
    
    let current = transcriptContent.querySelector('.current-transcript');
    if (!current) {
        current = document.createElement('p');
        current.className = 'transcript-line assistant current-transcript';
        current.style.opacity = '0.7';
        transcriptContent.appendChild(current);
    }
    
    current.innerHTML = `<strong>AI:</strong> ${text}`;
    transcriptPanel.scrollTop = transcriptPanel.scrollHeight;
}

/**
 * Clear transcripts
 */
function clearTranscripts() {
    transcriptContent.innerHTML = '';
    transcriptPanel.classList.add('hidden');
}

/**
 * Show error message
 */
function showError(message) {
    const toast = document.getElementById('error-toast');
    toast.textContent = '⚠️ ' + message;
    toast.classList.remove('hidden');
    
    setTimeout(() => {
        toast.classList.add('hidden');
    }, 5000);
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', init);
