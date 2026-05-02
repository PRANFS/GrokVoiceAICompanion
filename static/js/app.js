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
let selectedLanguage = 'en';
let currentBackgroundUrl = null;
let dynamicBgEnabled = true;
const PIPELINE_REALTIME_AGENT = 'realtime_agent';
const PIPELINE_LOCAL_STT_TTS = 'local_stt_tts';
let currentPipelineMode = localStorage.getItem('pipeline_mode') || PIPELINE_REALTIME_AGENT;

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
const subtitleDisplay = document.getElementById('subtitle-display');
const subtitleText = document.getElementById('subtitle-text');
const dynamicBackground = document.getElementById('dynamic-background');
const dynamicBgToggle = document.getElementById('dynamic-bg-toggle');
const visionIndicator = document.getElementById('vision-indicator');
const modeRealtimeBtn = document.getElementById('mode-realtime-btn');
const modeLocalBtn = document.getElementById('mode-local-btn');
const pipelineModeDescription = document.getElementById('pipeline-mode-description');

// Personality Modal Elements
const personalityBtn = document.getElementById('personality-btn');
const personalityModal = document.getElementById('personality-modal');
const closeModalBtn = document.getElementById('close-modal-btn');
const personalityLanguageSelect = document.getElementById('personality-language-select');
const voiceGenderFilter = document.getElementById('voice-gender-filter');
const voiceSelect = document.getElementById('voice-select');
const instructionsTextarea = document.getElementById('instructions-textarea');
const cancelPersonalityBtn = document.getElementById('cancel-personality-btn');
const savePersonalityBtn = document.getElementById('save-personality-btn');

// Voice cache & gender filter state
let allVoices = [];
let currentGenderFilter = 'all';

// Language display names — populated dynamically from /config or /voices
let languageNames = {};

/**
 * Initialize the application
 */
async function init() {
    console.log('🚀 Initializing Grok Voice AI Companion...');

    // Load personality (language) and voice list early so selectedLanguage is correct
    await loadPersonalityAndVoices();

    applyPipelineModeUI();

    // Bind controls first so mode switching still works even if avatar loading is slow.
    setupEventListeners();

    // Initialize WebSocket client
    wsClient = new GrokWebSocketClient({
        wsUrl: `ws://${window.location.host}/ws`,
        
        onStateChange: (state) => {
            updateStatus(state);
        },
        
        onTranscript: (text, role, isFinal, englishText = null) => {
            if (isFinal) {
                addTranscript(text, role, englishText);
                
                // Vision trigger detection is handled inside the WebSocket client
                // (via _onUserTranscriptReceived) so the audio hold buffer works.
                // Show the indicator if a vision query was just triggered.
                if (role === 'user' && wsClient.visionPending) {
                    showVisionIndicator('Analyzing...');
                }
            } else {
                updateCurrentTranscript(text);
            }
        },
        
        onSpeakingChange: (speaking, type) => {
            if (type === 'assistant') {
                updateStatus(speaking ? 'speaking' : 'connected');
                if (!speaking) {
                    // Extra force-close when Grok finishes response
                    lipSyncValue = 0;
                    avatar?.setLipSync(0);
                }
            }
        },
        
        onVolumeChange: (volume, type, vowel = null) => {
            console.log(`[App] onVolumeChange: volume=${volume.toFixed(2)}, type=${type}, vowel=${vowel}`);
            
            // Only animate avatar when AI is speaking, not when user speaks
            if (type === 'assistant') {
                // More responsive smoothing for faster lip sync
                lipSyncValue = lipSyncValue * 0.1 + volume * 0.9;
                
                console.log(`[App] Updating avatar lip sync: lipSyncValue=${lipSyncValue.toFixed(2)}, vowel=${vowel}`);
                // Pass vowel to avatar for proper mouth shape
                avatar?.setLipSync(lipSyncValue, vowel);
            } else {
                console.log(`[App] Skipping avatar update (type=${type})`);
            }
            
            // Update volume bars for both user and assistant
            updateVolumeBars(volume);
        },
        
        onBackgroundUpdate: (imageUrl, topic) => {
            console.log(`[App] Background update: ${topic}`);
            updateBackground(imageUrl, topic);
        },
        
        onVisionResponse: (text) => {
            console.log(`[App] Vision response: ${text?.substring(0, 80)}`);
            hideVisionIndicator();
            // Speech and transcript lines are emitted through the normal response path.
        },
        
        onError: (err) => {
            console.error('WebSocket error:', err);
            showError(err.error?.message || 'Connection error');
        }
    });
    
    // Initialize Live2D Avatar after controls are ready.
    try {
        avatar = new Live2DAvatar('live2d-canvas');
        avatar.onLoad = () => {
            console.log('✅ Avatar loaded');
            if (currentPipelineMode === PIPELINE_REALTIME_AGENT) {
                micButton.disabled = false;
            }
        };
        avatar.onError = (error) => {
            console.error('Avatar error:', error);
            showError('Failed to load avatar');
        };

        await avatar.loadModel(modelDropdown.value);
    } catch (error) {
        console.error('Failed to initialize avatar:', error);
        showError('Avatar failed to load. Voice controls are still available.');
    }

    if (currentPipelineMode === PIPELINE_LOCAL_STT_TTS) {
        await ensureLocalModeSession();
    }
}

/**
 * Setup event listeners
 */
function setupEventListeners() {
    // Mic button
    micButton.addEventListener('click', toggleConversation);

    // Pipeline mode buttons
    modeRealtimeBtn?.addEventListener('click', () => {
        setPipelineMode(PIPELINE_REALTIME_AGENT).catch((error) => {
            console.error('Failed to switch to realtime mode:', error);
            showError('Failed to switch mode');
        });
    });

    modeLocalBtn?.addEventListener('click', () => {
        setPipelineMode(PIPELINE_LOCAL_STT_TTS).catch((error) => {
            console.error('Failed to switch to local mode:', error);
            showError('Failed to switch mode');
        });
    });
    
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
        // Don't trigger shortcuts when typing in input fields
        const activeElement = document.activeElement;
        const isTyping = activeElement.tagName === 'INPUT' || 
                         activeElement.tagName === 'TEXTAREA' || 
                         activeElement.isContentEditable;
        
        if (e.code === 'Space' && !e.repeat && !isTyping) {
            e.preventDefault();
            toggleConversation();
        }
        // Close modal with Escape key
        if (e.code === 'Escape' && !personalityModal.classList.contains('hidden')) {
            closePersonalityModal();
        }
    });
    
    // Personality modal event listeners
    personalityBtn.addEventListener('click', openPersonalityModal);
    closeModalBtn.addEventListener('click', closePersonalityModal);
    cancelPersonalityBtn.addEventListener('click', closePersonalityModal);
    savePersonalityBtn.addEventListener('click', savePersonality);

    // Gender filter buttons
    if (voiceGenderFilter) {
        voiceGenderFilter.querySelectorAll('.gender-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                voiceGenderFilter.querySelectorAll('.gender-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                currentGenderFilter = btn.dataset.gender;
                populateVoiceSelect();
            });
        });
    }

    // Personality language change → re-filter voices
    if (personalityLanguageSelect) {
        personalityLanguageSelect.addEventListener('change', () => {
            populateVoiceSelect();
        });
    }
    
    // Dynamic background toggle
    if (dynamicBgToggle) {
        dynamicBgToggle.addEventListener('change', () => {
            dynamicBgEnabled = dynamicBgToggle.checked;
            console.log(`🖼️ Dynamic backgrounds: ${dynamicBgEnabled ? 'ON' : 'OFF'}`);
            if (isSessionActive && wsClient) {
                wsClient.sendDynamicBgToggle(dynamicBgEnabled);
            }
        });
    }
    
    // Close modal when clicking outside
    personalityModal.addEventListener('click', (e) => {
        if (e.target === personalityModal) {
            closePersonalityModal();
        }
    });
}

/**
 * Apply UI state for selected pipeline mode.
 */
function applyPipelineModeUI() {
    const isLocalMode = currentPipelineMode === PIPELINE_LOCAL_STT_TTS;

    modeRealtimeBtn?.classList.toggle('active', !isLocalMode);
    modeLocalBtn?.classList.toggle('active', isLocalMode);

    if (isLocalMode) {
        selectedLanguage = 'en';
        micButton.classList.add('hidden');
        micButton.disabled = true;
        if (pipelineModeDescription) {
            pipelineModeDescription.textContent =
                'Moonshine STT + Grok chat + Grok streaming TTS (English only)';
        }
    } else {
        micButton.classList.remove('hidden');
        micButton.disabled = false;
        if (pipelineModeDescription) {
            pipelineModeDescription.textContent = 'Realtime Grok Voice Agent mode';
        }
    }
}

/**
 * Switch between realtime and local pipeline modes.
 */
async function setPipelineMode(mode) {
    if (mode !== PIPELINE_REALTIME_AGENT && mode !== PIPELINE_LOCAL_STT_TTS) {
        return;
    }

    if (mode === currentPipelineMode) {
        if (mode === PIPELINE_LOCAL_STT_TTS) {
            await ensureLocalModeSession();
        }
        return;
    }

    currentPipelineMode = mode;
    localStorage.setItem('pipeline_mode', currentPipelineMode);

    if (isSessionActive) {
        stopConversation();
    }

    applyPipelineModeUI();

    if (!wsClient) {
        console.warn('WebSocket client not ready yet; deferring mode connection.');
        return;
    }

    if (currentPipelineMode === PIPELINE_LOCAL_STT_TTS) {
        await ensureLocalModeSession();
    } else {
        updateStatus('disconnected');
    }
}

/**
 * Ensure local mode is actively listening.
 */
async function ensureLocalModeSession() {
    if (currentPipelineMode !== PIPELINE_LOCAL_STT_TTS || isSessionActive || !wsClient) {
        return;
    }

    await startConversation({ autoStarted: true });
}

/**
 * Toggle conversation on/off (realtime mode only).
 */
async function toggleConversation() {
    if (currentPipelineMode === PIPELINE_LOCAL_STT_TTS) {
        return;
    }

    if (isSessionActive) {
        stopConversation();
    } else {
        await startConversation();
    }
}

/**
 * Start conversation.
 */
async function startConversation({ autoStarted = false } = {}) {
    try {
        updateStatus('connecting');

        if (!wsClient) {
            showError('Connection is still initializing. Please try again.');
            updateStatus('disconnected');
            return;
        }

        // Local mode is currently English-only.
        if (currentPipelineMode === PIPELINE_LOCAL_STT_TTS) {
            selectedLanguage = 'en';
        }
        // selectedLanguage is already set from personality on init

        await wsClient.connect(selectedLanguage, currentPipelineMode);

        const success = await wsClient.startAudioCapture();
        if (success) {
            isSessionActive = true;

            wsClient.sendDynamicBgToggle(dynamicBgEnabled);
            if (currentPipelineMode === PIPELINE_REALTIME_AGENT) {
                micButton.classList.remove('inactive');
                micButton.classList.add('active');
                micButton.textContent = '🎤';
            }

            if (!autoStarted) {
                clearTranscripts();
            }
            updateStatus('connected');
        }
    } catch (error) {
        console.error('Failed to start conversation:', error);
        showError('Failed to start. Is the backend running?');
        updateStatus('disconnected');
    }
}

/**
 * Stop conversation.
 */
function stopConversation() {
    if (wsClient) {
        wsClient.stopAudioCapture();
        wsClient.disconnect();
    }

    isSessionActive = false;
    micButton.classList.remove('active');
    micButton.classList.add('inactive');
    micButton.textContent = '🎙️';

    updateStatus('disconnected');
    lipSyncValue = 0;
    avatar?.setLipSync(0);
}

/**
 * Update status display.
 */
function updateStatus(state) {
    statusDot.className = '';
    const isLocalMode = currentPipelineMode === PIPELINE_LOCAL_STT_TTS;

    switch (state) {
        case 'disconnected':
            statusDot.classList.add('disconnected');
            statusText.textContent = isLocalMode
                ? 'STT/TTS mode paused. Reconnect by selecting STT/TTS.'
                : 'Click microphone to start';
            break;
        case 'connecting':
            statusDot.classList.add('connecting');
            statusText.textContent = isLocalMode
                ? 'Starting Moonshine STT/TTS pipeline...'
                : 'Connecting...';
            break;
        case 'connected':
            statusDot.classList.add('connected');
            statusText.textContent = isLocalMode
                ? 'Always listening (STT/TTS, English only)'
                : (isSessionActive ? 'Listening...' : 'Connected');
            break;
        case 'speaking':
            statusDot.classList.add('speaking');
            statusText.textContent = isLocalMode ? 'AI Speaking (STT/TTS mode)...' : 'AI Speaking...';
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
 * Update dynamic background image
 */
function updateBackground(imageUrl, topic) {
    if (!imageUrl || !dynamicBackground) return;
    
    // Don't update if it's the same image
    if (currentBackgroundUrl === imageUrl) return;
    
    currentBackgroundUrl = imageUrl;
    
    // Preload the image
    const img = new Image();
    img.onload = () => {
        // Fade transition
        dynamicBackground.style.opacity = '0';
        
        setTimeout(() => {
            dynamicBackground.style.backgroundImage = `url(${imageUrl})`;
            dynamicBackground.style.opacity = '1';
            
            // Show topic indicator briefly
            showBackgroundNotification(topic);
        }, 500);
    };
    img.onerror = () => {
        console.error('Failed to load background image:', imageUrl);
    };
    img.src = imageUrl;
}

/**
 * Show a notification when background changes
 */
function showBackgroundNotification(topic) {
    const notification = document.getElementById('background-notification');
    if (!notification) return;
    
    notification.textContent = `🎨 Mood: ${topic}`;
    notification.classList.remove('hidden');
    notification.classList.add('show');
    
    setTimeout(() => {
        notification.classList.remove('show');
        setTimeout(() => {
            notification.classList.add('hidden');
        }, 500);
    }, 3000);
}

/**
 * Add transcript line
 */
function addTranscript(text, role, englishText = null) {
    transcriptPanel.classList.remove('hidden');
    
    // Remove the streaming transcript element to avoid duplication
    if (role === 'assistant') {
        const current = transcriptContent.querySelector('.current-transcript');
        if (current) current.remove();
    }
    
    const line = document.createElement('p');
    line.className = `transcript-line ${role}`;
    
    if (selectedLanguage !== 'en' && role === 'assistant' && englishText) {
        // Show English translation with original language below
        line.innerHTML = `<strong>AI:</strong> ${englishText}
            <span class="language-tag">${languageNames[selectedLanguage]}</span>
            <span class="original-text">${text}</span>`;
    } else {
        line.innerHTML = `<strong>${role === 'user' ? 'You:' : 'AI:'}</strong> ${text}`;
    }
    
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
    
    // Just show the text in the transcript panel (no subtitles)
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
 * Show vision processing indicator
 */
function showVisionIndicator(text) {
    if (visionIndicator) {
        visionIndicator.textContent = `👁️ ${text}`;
        visionIndicator.classList.remove('hidden');
        visionIndicator.classList.add('show');
    }
}

/**
 * Hide vision processing indicator
 */
function hideVisionIndicator() {
    if (visionIndicator) {
        visionIndicator.classList.remove('show');
        setTimeout(() => visionIndicator.classList.add('hidden'), 500);
    }
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

/**
 * Load personality settings and voices on startup.
 */
async function loadPersonalityAndVoices() {
    try {
        // Fetch personality + voices in parallel
        const [personalityRes, voicesRes] = await Promise.all([
            fetch('/personality'),
            fetch('/voices')
        ]);

        if (personalityRes.ok) {
            const pData = await personalityRes.json();
            selectedLanguage = pData.language || 'en';
            console.log(`🌐 Loaded personality language: ${selectedLanguage}`);
        }

        if (voicesRes.ok) {
            const vData = await voicesRes.json();
            allVoices = vData.voices || [];
            if (vData.languages) {
                languageNames = vData.languages;
            }
            console.log(`🎤 Loaded ${allVoices.length} voices, ${Object.keys(languageNames).length} languages`);
        }
    } catch (error) {
        console.error('Failed to load personality/voices:', error);
        // Fallback: keep defaults
    }
}

/**
 * Populate the voice <select> based on selected language + gender filter.
 */
function populateVoiceSelect() {
    if (!voiceSelect || !personalityLanguageSelect) return;

    const selectedLang = personalityLanguageSelect.value;
    const isEnglish = selectedLang === 'en';

    // Filter voices
    let filtered = allVoices.filter(v => {
        // Multilingual voices always appear for any language
        if (v.multilingual) return true;
        // English language shows both multilingual + English-specific voices
        if (isEnglish && v.base_language === 'en') return true;
        // Other languages: show voices matching that base_language
        if (!isEnglish && v.base_language === selectedLang) return true;
        return false;
    });

    // Apply gender filter
    if (currentGenderFilter === 'male') {
        filtered = filtered.filter(v => v.gender === 'male');
    } else if (currentGenderFilter === 'female') {
        filtered = filtered.filter(v => v.gender === 'female');
    }

    // Populate dropdown
    voiceSelect.innerHTML = '';
    if (filtered.length === 0) {
        voiceSelect.innerHTML = '<option value="">-- No voices match --</option>';
        return;
    }

    filtered.forEach(v => {
        const opt = document.createElement('option');
        opt.value = v.id;
        opt.textContent = `${v.name} (${v.gender === 'male' ? '♂' : '♀'} ${v.language})`;
        voiceSelect.appendChild(opt);
    });
}

/**
 * Populate the language <select> in the personality modal.
 */
function populateLanguageSelect() {
    if (!personalityLanguageSelect) return;

    personalityLanguageSelect.innerHTML = '';
    const langEntries = Object.entries(languageNames);
    if (langEntries.length === 0) {
        // Fallback
        const fallback = { en: 'English', ja: 'Japanese', ko: 'Korean', zh: 'Chinese', es: 'Spanish', fr: 'French', de: 'German' };
        Object.entries(fallback).forEach(([code, name]) => {
            const opt = document.createElement('option');
            opt.value = code;
            opt.textContent = name;
            personalityLanguageSelect.appendChild(opt);
        });
    } else {
        langEntries.forEach(([code, name]) => {
            const opt = document.createElement('option');
            opt.value = code;
            opt.textContent = name;
            personalityLanguageSelect.appendChild(opt);
        });
    }
}

/**
 * Open personality modal and load current settings
 */
async function openPersonalityModal() {
    try {
        // If voices not loaded yet, fetch now
        if (allVoices.length === 0) {
            const vRes = await fetch('/voices');
            if (vRes.ok) {
                const vData = await vRes.json();
                allVoices = vData.voices || [];
                if (vData.languages) languageNames = vData.languages;
            }
        }

        populateLanguageSelect();

        // Lock language for STT/TTS mode
        const isLocalMode = currentPipelineMode === PIPELINE_LOCAL_STT_TTS;
        if (personalityLanguageSelect) {
            personalityLanguageSelect.disabled = isLocalMode;
            if (isLocalMode) {
                personalityLanguageSelect.value = 'en';
            } else {
                personalityLanguageSelect.value = selectedLanguage;
            }
        }

        // Reset gender filter
        currentGenderFilter = 'all';
        if (voiceGenderFilter) {
            voiceGenderFilter.querySelectorAll('.gender-btn').forEach(b => b.classList.remove('active'));
            const allBtn = voiceGenderFilter.querySelector('[data-gender="all"]');
            if (allBtn) allBtn.classList.add('active');
        }

        // Fetch current personality settings
        const response = await fetch('/personality');
        const data = await response.json();

        // Populate voice dropdown based on current language + gender
        populateVoiceSelect();

        // Select the current voice (or first available)
        if (data.voice && allVoices.some(v => v.id === data.voice.toLowerCase())) {
            voiceSelect.value = data.voice.toLowerCase();
        } else if (voiceSelect.options.length > 0) {
            voiceSelect.selectedIndex = 0;
        }

        instructionsTextarea.value = data.instructions;

        // Show modal
        personalityModal.classList.remove('hidden');
    } catch (error) {
        console.error('Failed to load personality settings:', error);
        showError('Failed to load personality settings');
    }
}

/**
 * Close personality modal
 */
function closePersonalityModal() {
    personalityModal.classList.add('hidden');
}

/**
 * Save personality settings
 */
async function savePersonality() {
    const voice = voiceSelect.value;
    const language = personalityLanguageSelect?.value || 'en';
    const instructions = instructionsTextarea.value.trim();

    if (!voice) {
        showError('Please select a voice');
        return;
    }

    if (!instructions) {
        showError('Instructions cannot be empty');
        return;
    }

    savePersonalityBtn.disabled = true;
    savePersonalityBtn.textContent = 'Saving...';

    try {
        const response = await fetch('/personality', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                voice: voice,
                language: language,
                instructions: instructions
            })
        });

        const data = await response.json();

        if (data.success) {
            console.log('✅ Personality saved:', data);
            // Update selectedLanguage so next conversation uses it
            selectedLanguage = language;
            closePersonalityModal();

            // Show success message
            const toast = document.getElementById('error-toast');
            toast.textContent = '✅ Personality saved! Restart conversation to apply.';
            toast.style.background = 'rgba(107, 203, 119, 0.9)';
            toast.classList.remove('hidden');
            setTimeout(() => {
                toast.classList.add('hidden');
                toast.style.background = '';
            }, 3000);
        } else {
            showError(data.error || 'Failed to save personality');
        }
    } catch (error) {
        console.error('Failed to save personality:', error);
        showError('Failed to save personality settings');
    } finally {
        savePersonalityBtn.disabled = false;
        savePersonalityBtn.textContent = '💾 Save';
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', init);
