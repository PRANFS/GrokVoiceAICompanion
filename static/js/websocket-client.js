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
        this.onBackgroundUpdate = options.onBackgroundUpdate || (() => {});
        this.onVisionResponse = options.onVisionResponse || (() => {});
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
        
        // Webcam / Vision
        this.videoStream = null;
        this.videoElement = null;
        this.visionEnabled = false; // true once webcam is acquired
        this.visionPending = false; // debounce flag
        this.visionSuppressing = false; // true = suppress all audio/transcripts until vision response
        
        // Audio hold buffer: holds AI audio between speech_stopped and user transcript
        // so we can decide whether to play or discard (if vision trigger detected)
        this._holdingForTranscript = false;
        this._heldAudioChunks = [];     // ArrayBuffer[]
        this._heldTranscriptText = '';  // accumulated assistant transcript during hold
        this._holdTimeout = null;       // safety timeout to release held audio
        
        // Vision trigger phrases (lowercased substrings)
        this.visionTriggers = [
            'what am i holding', 'what\'m i holding',
            'look at this', 'look at that',
            'what is this', 'what\'s this',
            'what is that', 'what\'s that',
            'what do you see', 'can you see',
            'see what i', 'see this',
            'what do i have', 'what\'s in my hand',
            'what color is', 'describe what',
            'take a look', 'check this out',
            'what are these', 'show you',
            'i\'m showing you', 'i am showing you',
            'do you see', 'look here'
        ];
        
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
                console.log(`[WebSocket] User volume: ${this.currentVolume.toFixed(2)}, type=user`);
                this.onVolumeChange(this.currentVolume, 'user');
            }
        }, 50);
    }
    
    /**
     * Handle incoming WebSocket messages
     */
    handleMessage(event) {
        if (event.data instanceof Blob) {
            // Suppress audio blobs while waiting for vision response
            if (this.visionSuppressing) {
                console.log(`[WebSocket] Suppressing audio blob (vision pending)`);
                return;
            }
            // Hold audio during transcript wait
            if (this._holdingForTranscript) {
                event.data.arrayBuffer().then(buf => this._heldAudioChunks.push(buf));
                return;
            }
            console.log(`[WebSocket] Received BLOB message, size=${event.data.size}`);
            this.handleAudioBlob(event.data);
            return;
        }
        
        try {
            const message = JSON.parse(event.data);
            console.log(`[WebSocket] Received JSON message: type=${message.type}`);
            
            switch (message.type) {
                case 'connection.ready':
                    console.log('🟢 Connection ready');
                    break;
                    
                case 'response.audio.delta':
                    if (message.delta) {
                        if (this.visionSuppressing) {
                            break;
                        }
                        if (this._holdingForTranscript) {
                            // Decode and hold audio chunk
                            const bStr = atob(message.delta);
                            const bArr = new Uint8Array(bStr.length);
                            for (let i = 0; i < bStr.length; i++) bArr[i] = bStr.charCodeAt(i);
                            this._heldAudioChunks.push(bArr.buffer);
                            break;
                        }
                        console.log(`[WebSocket] Received audio.delta, length=${message.delta.length}`);
                        this.handleAudioDelta(message.delta);
                    }
                    break;
                    
                case 'response.audio_transcript.delta':
                    if (message.delta) {
                        if (this.visionSuppressing) break;
                        if (this._holdingForTranscript) {
                            this._heldTranscriptText += message.delta;
                            break;
                        }
                        this.currentTranscript += message.delta;
                        this.onTranscript(this.currentTranscript, 'assistant', false);
                    }
                    break;
                    
                case 'response.audio_transcript.done':
                    if (this.visionSuppressing) {
                        this.currentTranscript = '';
                        break;
                    }
                    if (this._holdingForTranscript) break; // will be handled on release
                    const transcript = message.transcript || this.currentTranscript;
                    const englishTranslation = message.english_translation || null;
                    this.onTranscript(transcript, 'assistant', true, englishTranslation);
                    this.currentTranscript = '';
                    break;
                    
                case 'input_audio_buffer.speech_started':
                    this.isUserSpeaking = true;
                    this.onSpeakingChange(true, 'user');
                    // Barge-in: if AI is currently speaking/playing, cut it off immediately
                    if (this.isSpeaking || this.isPlaying || this.audioQueue.length > 0) {
                        console.log('🛑 Barge-in detected — flushing AI audio');
                        this.flushAudioQueue();
                        this.currentTranscript = '';
                    }
                    break;
                    
                case 'input_audio_buffer.speech_stopped':
                    this.isUserSpeaking = false;
                    this.onSpeakingChange(false, 'user');
                    // Start holding AI audio until we get the user transcript
                    // so we can check for vision triggers before any audio plays
                    this._startHoldingAudio();
                    break;
                    
                case 'response.created':
                    if (this.visionSuppressing) break;
                    if (this._holdingForTranscript) break; // don't show speaking yet
                    this.setSpeaking(true);
                    break;
                    
                case 'response.done':
                    if (this.visionSuppressing) break;
                    if (this._holdingForTranscript) break;
                    this.setSpeaking(false);
                    break;
                    
                case 'conversation.item.input_audio_transcription.completed':
                    if (message.transcript) {
                        this.onTranscript(message.transcript, 'user', true);
                        // Now decide: vision trigger or normal playback?
                        this._onUserTranscriptReceived(message.transcript);
                    }
                    break;
                    
                case 'error':
                    console.error('❌ Grok error:', message.error);
                    this.onError({ type: 'grok', error: message.error });
                    break;
                    
                case 'background.update':
                    console.log('🎨 Background update:', message.topic);
                    if (message.image_url) {
                        this.onBackgroundUpdate(message.image_url, message.topic);
                    }
                    break;
                    
                case 'vision.response':
                    console.log('👁️ Vision response:', message.text?.substring(0, 80));
                    this.visionSuppressing = false;
                    this.visionPending = false;
                    this.onVisionResponse(message.text);
                    // Turn off the webcam after the response — it will be
                    // re-initialized on the next vision trigger if needed.
                    this.stopWebcam();
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
        console.log(`[WebSocket] handleAudioDelta: decoding base64, length=${base64Audio.length}`);
        const binaryString = atob(base64Audio);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
            bytes[i] = binaryString.charCodeAt(i);
        }
        console.log(`[WebSocket] Decoded ${bytes.length} bytes, queuing for playback`);
        this.queueAudioPlayback(bytes.buffer);
    }
    
    /**
     * Handle binary audio blob
     */
    async handleAudioBlob(blob) {
        console.log(`[WebSocket] handleAudioBlob: size=${blob.size}`);
        const arrayBuffer = await blob.arrayBuffer();
        console.log(`[WebSocket] Converted to arrayBuffer, size=${arrayBuffer.byteLength}, queuing for playback`);
        this.queueAudioPlayback(arrayBuffer);
    }
    
    /**
     * Queue audio for playback
     */
    queueAudioPlayback(arrayBuffer) {
        console.log(`[WebSocket] queueAudioPlayback: adding ${arrayBuffer.byteLength} bytes to queue. Queue length before: ${this.audioQueue.length}`);
        this.audioQueue.push(arrayBuffer);
        
        if (!this.isPlaying) {
            console.log(`[WebSocket] Starting playback (was not playing)`);
            this.playNextAudio();
        } else {
            console.log(`[WebSocket] Already playing, audio queued`);
        }
    }
    
    /**
     * Play queued audio
     */
    async playNextAudio() {
        console.log(`[WebSocket] playNextAudio called. Queue length: ${this.audioQueue.length}`);
        
        if (this.audioQueue.length === 0) {
            console.log(`[WebSocket] Queue empty, forcing final lip sync decay`);
            this.isPlaying = false;
            // Extra forced decay calls to ensure mouth closes fully
            this.onVolumeChange(0, 'assistant', null);
            setTimeout(() => this.onVolumeChange(0, 'assistant', null), 100);
            setTimeout(() => this.onVolumeChange(0, 'assistant', null), 200);
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
            
            // ENHANCED: Analyze for vowel detection
            const vowelInfo = this.analyzeVowelFromAudio(float32, this.SAMPLE_RATE);
            console.log(`[WebSocket] Calling onVolumeChange: intensity=${vowelInfo.intensity.toFixed(2)}, type=assistant, vowel=${vowelInfo.vowel}`);
            this.onVolumeChange(vowelInfo.intensity, 'assistant', vowelInfo.vowel);
            
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
     * Analyze audio for vowel detection
     * Uses formant frequency analysis to estimate vowels
     * 
     * Vowel formant frequencies (approximate):
     * A: F1 ~700-1000Hz, F2 ~1200-1400Hz
     * E: F1 ~400-600Hz, F2 ~2000-2400Hz
     * I: F1 ~300-400Hz, F2 ~2200-2700Hz
     * O: F1 ~500-700Hz, F2 ~800-1000Hz
     * U: F1 ~300-400Hz, F2 ~800-1200Hz
     */
    analyzeVowelFromAudio(float32Array, sampleRate = 24000) {
        const numSamples = float32Array.length;  // ← changed from min(..., 512)
        
        // Calculate energy, zero-crossing rate, and high-frequency content
        let zeroCrossings = 0;
        let energy = 0;
        let highFreqEnergy = 0;
        
        for (let i = 1; i < numSamples; i++) {
            // Zero crossing rate (indicates frequency)
            if ((float32Array[i] >= 0) !== (float32Array[i-1] >= 0)) {
                zeroCrossings++;
            }
            
            // Total energy
            energy += float32Array[i] * float32Array[i];
            
            // High frequency estimation (sample-to-sample difference)
            const diff = Math.abs(float32Array[i] - float32Array[i-1]);
            highFreqEnergy += diff * diff;
        }
        
        energy = Math.sqrt(energy / numSamples);
        highFreqEnergy = Math.sqrt(highFreqEnergy / numSamples);
        const zcr = zeroCrossings / numSamples;
        
        if (energy < 0.005) {  // ← slightly lower threshold, was 0.01
        return { vowel: null, intensity: 0 };
        }
        
        // High-frequency ratio
        const hfRatio = highFreqEnergy / (energy + 0.001);
        
        // Estimate vowel based on acoustic characteristics
        // High ZCR + high HF = front vowels (I, E)
        // Low ZCR + low HF = back rounded vowels (O, U)
        // Medium = open vowels (A)
        
        let vowel = 'a'; // default
        
        if (zcr > 0.40 && hfRatio > 0.7) {
            // Very high frequency - likely 'I'
            vowel = 'i';
        } else if (zcr > 0.28 && hfRatio > 0.50) {
            // High-mid frequency - likely 'E'
            vowel = 'e';
        } else if (zcr < 0.13 && hfRatio < 0.22) {
            // Very low frequency - likely 'U'
            vowel = 'u';
        } else if (zcr < 0.18 && hfRatio < 0.30) {
            // Low-mid frequency - likely 'O'
            vowel = 'o';
        } else {
            // Middle range - likely 'A'
            vowel = 'a';
        }
        
        const intensity = Math.min(1, energy * 40);  // ← was *50, slightly reduced
        
        return { vowel, intensity };
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
    
    // ─── Audio Hold Buffer (vision trigger detection) ─────────────────
    
    /**
     * Start holding AI audio after user finishes speaking.
     * All AI audio/transcripts are buffered until the user's transcript
     * arrives so we can check for vision triggers BEFORE audio plays.
     */
    _startHoldingAudio() {
        this._holdingForTranscript = true;
        this._heldAudioChunks = [];
        this._heldTranscriptText = '';
        
        // Safety timeout: if user transcript never arrives within 3s,
        // release the buffer anyway so audio isn't lost
        if (this._holdTimeout) clearTimeout(this._holdTimeout);
        this._holdTimeout = setTimeout(() => {
            if (this._holdingForTranscript) {
                console.log('[WebSocket] Hold timeout — releasing buffered audio');
                this._releaseHeldAudio();
            }
        }, 3000);
        
        console.log('[WebSocket] Holding AI audio until user transcript arrives');
    }
    
    /**
     * Called when the user's final transcript arrives.
     * Decides whether to play or discard the held audio.
     */
    _onUserTranscriptReceived(text) {
        if (!this._holdingForTranscript) return;
        
        if (this._holdTimeout) {
            clearTimeout(this._holdTimeout);
            this._holdTimeout = null;
        }
        
        if (this.containsVisionTrigger(text)) {
            // Vision trigger detected — discard all held audio
            console.log(`👁️ Vision trigger in held audio — discarding ${this._heldAudioChunks.length} chunks`);
            this._holdingForTranscript = false;
            this._heldAudioChunks = [];
            this._heldTranscriptText = '';
            this.currentTranscript = '';
            
            // Set suppression for any remaining chunks still in-flight
            this.visionSuppressing = true;
            // NOTE: Do NOT set visionPending here — sendVisionQuery() manages
            // that flag itself, and setting it early causes the method to
            // bail out immediately ("already pending") without ever sending.
            this.flushAudioQueue();
            
            // Send the vision query (handle failure to avoid permanent suppression)
            this.sendVisionQuery(text).then(success => {
                if (!success) {
                    console.log('⚠️ Vision query failed — resuming normal audio');
                    this.visionSuppressing = false;
                    this.visionPending = false;
                }
            });
        } else {
            // Normal speech — release the held audio for playback
            this._releaseHeldAudio();
        }
    }
    
    /**
     * Release all held audio chunks into the normal playback queue.
     */
    _releaseHeldAudio() {
        this._holdingForTranscript = false;
        
        if (this._holdTimeout) {
            clearTimeout(this._holdTimeout);
            this._holdTimeout = null;
        }
        
        const chunks = this._heldAudioChunks;
        const heldText = this._heldTranscriptText;
        this._heldAudioChunks = [];
        this._heldTranscriptText = '';
        
        console.log(`[WebSocket] Releasing ${chunks.length} held audio chunks`);
        
        // Show speaking state now
        if (chunks.length > 0) {
            this.setSpeaking(true);
        }
        
        // Replay held transcript
        if (heldText) {
            this.currentTranscript = heldText;
            this.onTranscript(heldText, 'assistant', false);
        }
        
        // Queue all held audio for playback
        for (const chunk of chunks) {
            this.queueAudioPlayback(chunk);
        }
    }
    
    // ─── Webcam / Vision Methods ────────────────────────────────────────
    
    /**
     * Initialize webcam for vision queries.
     * Creates a hidden <video> element and starts the camera stream.
     */
    async initWebcam() {
        if (this.visionEnabled) return true;
        
        try {
            this.videoStream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } }
            });
            
            // Create hidden video element for frame capture
            this.videoElement = document.createElement('video');
            this.videoElement.srcObject = this.videoStream;
            this.videoElement.setAttribute('playsinline', '');
            this.videoElement.muted = true;
            await this.videoElement.play();
            
            this.visionEnabled = true;
            console.log('📷 Webcam initialized for vision');
            return true;
        } catch (err) {
            console.error('❌ Webcam access denied or unavailable:', err);
            this.onError({ type: 'webcam', error: err });
            return false;
        }
    }
    
    /**
     * Stop webcam stream.
     */
    stopWebcam() {
        if (this.videoStream) {
            this.videoStream.getTracks().forEach(t => t.stop());
            this.videoStream = null;
        }
        if (this.videoElement) {
            this.videoElement.srcObject = null;
            this.videoElement = null;
        }
        this.visionEnabled = false;
        console.log('📷 Webcam stopped');
    }
    
    /**
     * Capture a JPEG frame from the webcam and return base64 (no data-url prefix).
     */
    captureFrame() {
        if (!this.videoElement || !this.visionEnabled) return null;
        
        const canvas = document.createElement('canvas');
        canvas.width = this.videoElement.videoWidth || 1280;
        canvas.height = this.videoElement.videoHeight || 720;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(this.videoElement, 0, 0, canvas.width, canvas.height);
        
        // Return base64 without the data:image/jpeg;base64, prefix
        const dataUrl = canvas.toDataURL('image/jpeg', 0.8);
        return dataUrl.split(',')[1];
    }
    
    /**
     * Check if a transcript contains a vision trigger phrase.
     */
    containsVisionTrigger(text) {
        const lower = text.toLowerCase();
        return this.visionTriggers.some(trigger => lower.includes(trigger));
    }
    
    /**
     * Send a vision query: capture frame + send to backend.
     * @param {string} query - The user's spoken query text
     */
    async sendVisionQuery(query) {
        if (this.visionPending) {
            console.log('👁️ Vision query already pending, skipping');
            return false;
        }
        
        // Initialise webcam on first use
        if (!this.visionEnabled) {
            const ok = await this.initWebcam();
            if (!ok) return false;
            // Small delay for camera to warm up
            await new Promise(r => setTimeout(r, 500));
        }
        
        const frame = this.captureFrame();
        if (!frame) {
            console.error('❌ Failed to capture webcam frame');
            return false;
        }
        
        this.visionPending = true;
        this.visionSuppressing = true;
        
        // Immediately flush any audio that's already queued/playing from the
        // hallucinated response the Realtime API started before we could cancel
        this.flushAudioQueue();
        
        console.log(`👁️ Sending vision query: "${query}" (image size: ${Math.round(frame.length / 1024)}KB)`);
        
        if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: 'vision.query',
                image: frame,
                query: query
            }));
            return true;
        }
        
        this.visionPending = false;
        return false;
    }
    
    /**
     * Flush all queued/playing audio immediately.
     * Used when a vision query is triggered to silence the hallucinated response.
     */
    flushAudioQueue() {
        this.audioQueue = [];
        this.isPlaying = false;
        this.currentTranscript = '';
        
        // Close and recreate the playback context to stop any currently-playing buffer
        if (this.playbackContext) {
            this.playbackContext.close().catch(() => {});
            this.playbackContext = null;
        }
        
        // Reset lip sync
        this.onVolumeChange(0, 'assistant', null);
        this.setSpeaking(false);
        console.log('🔇 Flushed audio queue (vision query)');
    }
    
    /**
     * Toggle dynamic background generation on/off.
     */
    sendDynamicBgToggle(enabled) {
        if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: 'dynamic_bg.toggle',
                enabled: enabled
            }));
            console.log(`🖼️ Dynamic backgrounds ${enabled ? 'enabled' : 'disabled'}`);
        }
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
        this.stopWebcam();
        
        if (this.playbackContext) {
            this.playbackContext.close();
            this.playbackContext = null;
        }
        
        if (this._holdTimeout) {
            clearTimeout(this._holdTimeout);
            this._holdTimeout = null;
        }
        
        this.audioQueue = [];
        this.isPlaying = false;
        this.isSpeaking = false;
        this.currentVolume = 0;
        this.visionPending = false;
        this.visionSuppressing = false;
        this._holdingForTranscript = false;
        this._heldAudioChunks = [];
        this._heldTranscriptText = '';
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
