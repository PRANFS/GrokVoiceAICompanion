/**
 * WebSocket Client for Grok Voice API
 * 
 * Handles:
 * - WebSocket connection to FastAPI backend
 * - Audio capture from microphone (24kHz PCM16)
 * - Audio playback of Grok responses
 * - Speaking state detection for lip sync
 * - Language selection and English subtitles
 */

class GrokWebSocketClient {
    constructor(options = {}) {
        this.wsUrl = options.wsUrl || `ws://${window.location.host}/ws`;
        
        // Callbacks
        this.onStateChange = options.onStateChange || (() => {});
        this.onTranscript = options.onTranscript || (() => {});
        this.onSpeakingChange = options.onSpeakingChange || (() => {});
        this.onVolumeChange = options.onVolumeChange || (() => {});
        this.onError = options.onError || (() => {});
        
        // WebSocket
        this.ws = null;
        
        // Audio capture
        this.audioContext = null;
        this.mediaStream = null;
        this.sourceNode = null;
        this.processorNode = null;
        this.analyserNode = null;
        
        // Audio playback
        this.playbackContext = null;
        this.audioQueue = [];
        this.isPlaying = false;
        
        // State
        this.state = 'disconnected';
        this.isSpeaking = false;
        this.isUserSpeaking = false;
        this.currentTranscript = '';
        this.volumeCheckInterval = null;
        this.currentVolume = 0;
        this.selectedLanguage = 'en';
        
        // Audio config (24kHz is xAI's default and most reliable)
        this.SAMPLE_RATE = 24000;
        this.BUFFER_SIZE = 4096;
    }
    
    /**
     * Connect to WebSocket server
     */
    async connect(language = 'en') {
        if (this.ws?.readyState === WebSocket.OPEN) {
            console.log('Already connected');
            return;
        }
        
        this.selectedLanguage = language;
        this.setState('connecting');
        
        return new Promise((resolve, reject) => {
            try {
                // Add language as query parameter
                const wsUrlWithLang = `${this.wsUrl}?language=${language}`;
                this.ws = new WebSocket(wsUrlWithLang);
                
                this.ws.onopen = () => {
                    console.log('✅ Connected to WebSocket server');
                    this.setState('connected');
                    resolve();
                };
                
                this.ws.onmessage = (event) => this.handleMessage(event);
                
                this.ws.onerror = (error) => {
                    console.error('❌ WebSocket error:', error);
                    this.onError({ type: 'websocket', error });
                    reject(error);
                };
                
                this.ws.onclose = (event) => {
                    console.log('🔌 WebSocket closed:', event.code);
                    this.setState('disconnected');
                    this.cleanup();
                };
                
            } catch (error) {
                console.error('Failed to connect:', error);
                this.setState('disconnected');
                reject(error);
            }
        });
    }
    
    /**
     * Send language change notification to server
     */
    sendLanguageChange(language) {
        if (this.ws?.readyState === WebSocket.OPEN) {
            this.selectedLanguage = language;
            this.ws.send(JSON.stringify({
                type: 'language.change',
                language: language
            }));
            console.log(`🌐 Sent language change: ${language}`);
        }
    }
    
    /**
     * Disconnect
     */
    disconnect() {
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        this.cleanup();
        this.setState('disconnected');
    }
    
    /**
     * Start audio capture from microphone
     */
    async startAudioCapture() {
        try {
            // Request microphone
            this.mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    sampleRate: this.SAMPLE_RATE,
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true
                }
            });
            
            // Create audio context
            this.audioContext = new AudioContext({ sampleRate: this.SAMPLE_RATE });
            
            // Create source from mic
            this.sourceNode = this.audioContext.createMediaStreamSource(this.mediaStream);
            
            // Create analyser for volume detection
            this.analyserNode = this.audioContext.createAnalyser();
            this.analyserNode.fftSize = 256;
            this.sourceNode.connect(this.analyserNode);
            
            // Create processor for audio data
            this.processorNode = this.audioContext.createScriptProcessor(this.BUFFER_SIZE, 1, 1);
            
            this.processorNode.onaudioprocess = (event) => {
                if (this.ws?.readyState !== WebSocket.OPEN) return;
                
                const inputData = event.inputBuffer.getChannelData(0);
                const pcm16 = this.float32ToPCM16(inputData);
                
                // Send as binary
                this.ws.send(pcm16.buffer);
            };
            
            this.sourceNode.connect(this.processorNode);
            this.processorNode.connect(this.audioContext.destination);
            
            // Start volume monitoring
            this.startVolumeMonitoring();
            
            console.log('🎤 Audio capture started');
            return true;
            
        } catch (error) {
            console.error('Failed to start audio capture:', error);
            this.onError({ type: 'microphone', error });
            return false;
        }
    }
    
    /**
     * Stop audio capture
     */
    stopAudioCapture() {
        if (this.volumeCheckInterval) {
            clearInterval(this.volumeCheckInterval);
            this.volumeCheckInterval = null;
        }
        
        if (this.processorNode) {
            this.processorNode.disconnect();
            this.processorNode = null;
        }
        
        if (this.sourceNode) {
            this.sourceNode.disconnect();
            this.sourceNode = null;
        }
        
        if (this.audioContext) {
            this.audioContext.close();
            this.audioContext = null;
        }
        
        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach(track => track.stop());
            this.mediaStream = null;
        }
        
        console.log('🔇 Audio capture stopped');
    }
    
    /**
     * Monitor volume for lip sync
     */
    startVolumeMonitoring() {
        if (!this.analyserNode) return;
        
        const dataArray = new Uint8Array(this.analyserNode.frequencyBinCount);
        
        this.volumeCheckInterval = setInterval(() => {
            this.analyserNode.getByteFrequencyData(dataArray);
            
            let sum = 0;
            for (let i = 0; i < dataArray.length; i++) {
                sum += dataArray[i];
            }
            const average = sum / dataArray.length;
            
            this.currentVolume = Math.min(1, average / 128);
            
            const wasSpeaking = this.isUserSpeaking;
            this.isUserSpeaking = this.currentVolume > 0.1;
            
            if (wasSpeaking !== this.isUserSpeaking) {
                this.onSpeakingChange(this.isUserSpeaking, 'user');
            }
            
            if (!this.isSpeaking) {
                this.onVolumeChange(this.currentVolume, 'user');
            }
        }, 50);
    }
    
    /**
     * Handle incoming WebSocket messages
     */
    handleMessage(event) {
        if (event.data instanceof Blob) {
            this.handleAudioBlob(event.data);
            return;
        }
        
        try {
            const message = JSON.parse(event.data);
            
            switch (message.type) {
                case 'connection.ready':
                    console.log('🟢 Connection ready');
                    break;
                    
                case 'response.audio.delta':
                    if (message.delta) {
                        this.handleAudioDelta(message.delta);
                    }
                    break;
                    
                case 'response.audio_transcript.delta':
                    if (message.delta) {
                        this.currentTranscript += message.delta;
                        // Pass transcript to UI for display and subtitles
                        this.onTranscript(this.currentTranscript, 'assistant', false);
                    }
                    break;
                    
                case 'response.audio_transcript.done':
                    const transcript = message.transcript || this.currentTranscript;
                    const englishTranslation = message.english_translation || null;
                    this.onTranscript(transcript, 'assistant', true, englishTranslation);
                    this.currentTranscript = '';
                    break;
                    
                case 'input_audio_buffer.speech_started':
                    this.isUserSpeaking = true;
                    this.onSpeakingChange(true, 'user');
                    break;
                    
                case 'input_audio_buffer.speech_stopped':
                    this.isUserSpeaking = false;
                    this.onSpeakingChange(false, 'user');
                    break;
                    
                case 'response.created':
                    this.setSpeaking(true);
                    break;
                    
                case 'response.done':
                    this.setSpeaking(false);
                    break;
                    
                case 'conversation.item.input_audio_transcription.completed':
                    if (message.transcript) {
                        this.onTranscript(message.transcript, 'user', true);
                    }
                    break;
                    
                case 'error':
                    console.error('❌ Grok error:', message.error);
                    this.onError({ type: 'grok', error: message.error });
                    break;
            }
        } catch (error) {
            console.error('Failed to parse message:', error);
        }
    }
    
    /**
     * Handle audio delta (base64)
     */
    handleAudioDelta(base64Audio) {
        const binaryString = atob(base64Audio);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
            bytes[i] = binaryString.charCodeAt(i);
        }
        this.queueAudioPlayback(bytes.buffer);
    }
    
    /**
     * Handle binary audio blob
     */
    async handleAudioBlob(blob) {
        const arrayBuffer = await blob.arrayBuffer();
        this.queueAudioPlayback(arrayBuffer);
    }
    
    /**
     * Queue audio for playback
     */
    queueAudioPlayback(arrayBuffer) {
        this.audioQueue.push(arrayBuffer);
        
        if (!this.isPlaying) {
            this.playNextAudio();
        }
    }
    
    /**
     * Play queued audio
     */
    async playNextAudio() {
        if (this.audioQueue.length === 0) {
            this.isPlaying = false;
            return;
        }
        
        this.isPlaying = true;
        
        if (!this.playbackContext) {
            this.playbackContext = new AudioContext({ sampleRate: this.SAMPLE_RATE });
        }
        
        // Ensure audio context is running (prevents first syllable cutoff)
        if (this.playbackContext.state === 'suspended') {
            await this.playbackContext.resume();
        }
        
        const audioData = this.audioQueue.shift();
        
        try {
            // Convert PCM16 to Float32
            const pcm16 = new Int16Array(audioData);
            const float32 = new Float32Array(pcm16.length);
            
            for (let i = 0; i < pcm16.length; i++) {
                float32[i] = pcm16[i] / 32768.0;
            }
            
            // Create audio buffer
            const audioBuffer = this.playbackContext.createBuffer(1, float32.length, this.SAMPLE_RATE);
            audioBuffer.copyToChannel(float32, 0);
            
            // Create source and play
            const source = this.playbackContext.createBufferSource();
            source.buffer = audioBuffer;
            
            // Add small gain ramp to prevent audio clicks/pops at start
            const gainNode = this.playbackContext.createGain();
            gainNode.gain.setValueAtTime(0, this.playbackContext.currentTime);
            gainNode.gain.linearRampToValueAtTime(1, this.playbackContext.currentTime + 0.03);
            
            source.connect(gainNode);
            gainNode.connect(this.playbackContext.destination);
            
            // Calculate volume for lip sync
            const rms = this.calculateRMS(float32);
            this.onVolumeChange(Math.min(1, rms * 5), 'assistant');
            
            source.onended = () => this.playNextAudio();
            source.start();
            
        } catch (error) {
            console.error('Failed to play audio:', error);
            this.playNextAudio();
        }
    }
    
    /**
     * Calculate RMS volume
     */
    calculateRMS(samples) {
        let sum = 0;
        for (let i = 0; i < samples.length; i++) {
            sum += samples[i] * samples[i];
        }
        return Math.sqrt(sum / samples.length);
    }
    
    /**
     * Convert Float32 to Int16 PCM
     */
    float32ToPCM16(float32Array) {
        const pcm16 = new Int16Array(float32Array.length);
        for (let i = 0; i < float32Array.length; i++) {
            const sample = Math.max(-1, Math.min(1, float32Array[i]));
            pcm16[i] = sample < 0 ? sample * 0x8000 : sample * 0x7FFF;
        }
        return pcm16;
    }
    
    /**
     * Send text message
     */
    sendText(text) {
        if (this.ws?.readyState !== WebSocket.OPEN) return false;
        
        const message = {
            type: 'conversation.item.create',
            item: {
                type: 'message',
                role: 'user',
                content: [{ type: 'input_text', text }]
            }
        };
        
        this.ws.send(JSON.stringify(message));
        this.ws.send(JSON.stringify({ type: 'response.create' }));
        
        return true;
    }
    
    /**
     * Set speaking state
     */
    setSpeaking(speaking) {
        this.isSpeaking = speaking;
        this.onSpeakingChange(speaking, 'assistant');
    }
    
    /**
     * Set connection state
     */
    setState(state) {
        this.state = state;
        this.onStateChange(state);
    }
    
    /**
     * Cleanup
     */
    cleanup() {
        this.stopAudioCapture();
        
        if (this.playbackContext) {
            this.playbackContext.close();
            this.playbackContext = null;
        }
        
        this.audioQueue = [];
        this.isPlaying = false;
        this.isSpeaking = false;
        this.currentVolume = 0;
    }
    
    /**
     * Get current state
     */
    getState() {
        return {
            connectionState: this.state,
            isSpeaking: this.isSpeaking,
            isUserSpeaking: this.isUserSpeaking,
            volume: this.currentVolume
        };
    }
}

// Export
window.GrokWebSocketClient = GrokWebSocketClient;
