/**
 * Live2D Avatar Controller
 * 
 * Handles Live2D model loading, rendering, and animations
 * using PixiJS and pixi-live2d-display
 */

class Live2DAvatar {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        this.app = null;
        this.model = null;
        this.isLoaded = false;
        
        // Animation state
        this.animationState = {
            time: 0,
            blinking: false,
            blinkProgress: 0,
            breathValue: 0,
            headTiltX: 0,
            headTiltY: 0,
            headTiltZ: 0,
            targetHeadX: 0,
            targetHeadY: 0,
            eyeLookX: 0,
            eyeLookY: 0
        };
        
        // Parameter names
        this.PARAMS = {
            MOUTH_OPEN_Y: 'ParamMouthOpenY',
            MOUTH_FORM: 'ParamMouthForm',
            EYE_L_OPEN: 'ParamEyeLOpen',
            EYE_R_OPEN: 'ParamEyeROpen',
            EYE_BALL_X: 'ParamEyeBallX',
            EYE_BALL_Y: 'ParamEyeBallY',
            ANGLE_X: 'ParamAngleX',
            ANGLE_Y: 'ParamAngleY',
            ANGLE_Z: 'ParamAngleZ',
            BODY_ANGLE_X: 'ParamBodyAngleX',
            BODY_ANGLE_Y: 'ParamBodyAngleY',
            BODY_ANGLE_Z: 'ParamBodyAngleZ',
            BREATH: 'ParamBreath',
            // Vowel lip sync parameters (for kei_vowels_pro)
            VOWEL_A: 'ParamA',
            VOWEL_I: 'ParamI',
            VOWEL_U: 'ParamU',
            VOWEL_E: 'ParamE',
            VOWEL_O: 'ParamO'
        };
        
        // Lip sync state for smooth transitions
        this.lipSyncState = {
            currentVowel: null,
            mouthOpen: 0,
            vowelA: 0,
            vowelI: 0,
            vowelU: 0,
            vowelE: 0,
            vowelO: 0
        };
        
        this.blinkTimer = null;
        this.animationFrame = null;
        
        // Callbacks
        this.onLoad = null;
        this.onError = null;
    }
    
    /**
     * Initialize PixiJS application
     */
    init() {
        if (this.app) return;
        
        // PixiJS v7 uses synchronous constructor
        this.app = new PIXI.Application({
            view: this.canvas,
            width: window.innerWidth,
            height: window.innerHeight,
            backgroundAlpha: 0,
            resolution: window.devicePixelRatio || 1,
            autoDensity: true,
            antialias: true
        });
        
        // Handle window resize
        window.addEventListener('resize', () => this.handleResize());
        
        // Start animation loop
        this.startAnimationLoop();
        
        // Schedule random blinks
        this.scheduleBlink();
    }
    
    /**
     * Load a Live2D model
     */
    async loadModel(modelPath) {
        try {
            // Show loading
            document.getElementById('loading-overlay').classList.remove('hidden');
            
            // Initialize if needed
            if (!this.app) {
                this.init();
            }
            
            // Remove existing model
            if (this.model) {
                this.app.stage.removeChild(this.model);
                this.model.destroy();
                this.model = null;
            }
            
            console.log('Loading Live2D model from:', modelPath);
            
            // Load the model using Cubism4 (for .model3.json files)
            // Load the model
            this.model = await PIXI.live2d.Live2DModel.from(modelPath, {
                autoInteract: false,
                autoUpdate: true
            });
            
            // Scale and position
            this.positionModel();
            
            // Add to stage
            this.app.stage.addChild(this.model);
            
            // Enable interaction
            this.model.eventMode = 'static';
            this.model.cursor = 'pointer';
            this.model.interactive = true;
            
            // Add eye tracking on mouse move
            this.model.on('pointermove', (e) => {
                const { x, y } = e.global;
                const bounds = this.model.getBounds();
                this.animationState.eyeLookX = ((x - bounds.x) / bounds.width - 0.5) * 2;
                this.animationState.eyeLookY = ((y - bounds.y) / bounds.height - 0.5) * 2;
            });
            
            // Disable ALL motion sounds globally
            if (this.model.internalModel?.motionManager) {
                this.model.internalModel.motionManager.settings.sound = '';
                
                // Also disable sounds in all motion definitions
                const definitions = this.model.internalModel.motionManager.definitions;
                if (definitions) {
                    for (const group of Object.keys(definitions)) {
                        const motions = definitions[group];
                        if (Array.isArray(motions)) {
                            for (const motion of motions) {
                                if (motion) {
                                    motion.Sound = undefined;
                                    motion.sound = undefined;
                                    motion.SoundPath = undefined;
                                }
                            }
                        }
                    }
                }
            }
            
            // Disable the internal sound manager if it exists
            if (this.model.internalModel?.coreModel?.motionManager?.sound) {
                this.model.internalModel.coreModel.motionManager.sound = null;
            }
            
            // Add click interaction for hit areas
            this.model.on('pointerdown', (e) => {
                // Convert screen coordinates to model-space coordinates
                const point = e.data.getLocalPosition(this.model);
                console.log('Click detected at screen:', e.global.x, e.global.y);
                console.log('Click in model space:', point.x, point.y);
                
                // Check if hit area was clicked using model-space coordinates
                const hitAreaNames = this.model.internalModel?.hitTest(point.x, point.y);
                console.log('Hit areas:', hitAreaNames);
                
                if (hitAreaNames && hitAreaNames.length > 0) {
                    console.log('Hit area clicked:', hitAreaNames[0]);
                    
                    // Play a random motion with PRIORITY
                    const motionGroups = Object.keys(this.model.internalModel.motionManager.definitions);
                    console.log('Available motion groups:', motionGroups);
                    
                    if (motionGroups.length > 0) {
                        // Use the first motion group (usually the default group)
                        const group = motionGroups[0];
                        const motionCount = this.model.internalModel.motionManager.definitions[group].length;
                        const motionIndex = Math.floor(Math.random() * motionCount);
                        
                        console.log(`Playing motion: group="${group}", index=${motionIndex}`);
                        // Play motion with force priority (3 = force, overrides idle animations)
                        this.model.motion(group, motionIndex, 3);
                    }
                } else {
                    console.log('No hit area detected at this position');
                }
            });
            
            this.isLoaded = true;
            document.getElementById('loading-overlay').classList.add('hidden');
            
            if (this.onLoad) this.onLoad(this.model);
            console.log('Live2D model loaded successfully');
            
        } catch (error) {
            console.error('Failed to load Live2D model:', error);
            document.getElementById('loading-overlay').classList.add('hidden');
            
            if (this.onError) this.onError(error);
            this.showError('Failed to load avatar: ' + error.message);
        }
    }
    
    /**
     * Position and scale model
     */
    positionModel() {
        if (!this.model) return;
        
        const scale = Math.min(
            (window.innerWidth * 0.8) / this.model.width,
            (window.innerHeight * 0.9) / this.model.height
        );
        
        this.model.scale.set(scale);
        this.model.anchor.set(0.5, 0.5);
        this.model.x = window.innerWidth / 2;
        this.model.y = window.innerHeight / 2 + 50;
    }
    
    /**
     * Handle window resize
     */
    handleResize() {
        if (this.app) {
            this.app.renderer.resize(window.innerWidth, window.innerHeight);
        }
        this.positionModel();
    }
    
    /**
     * Set model parameter safely
     */
    setParam(paramName, value) {
        if (!this.model?.internalModel?.coreModel) return;
        
        try {
            const coreModel = this.model.internalModel.coreModel;
            const paramIndex = coreModel.getParameterIndex(paramName);
            
            if (paramIndex >= 0) {
                coreModel.setParameterValueByIndex(paramIndex, value);
            }
        } catch (e) {
            // Parameter might not exist
        }
    }
    
    /**
     * Trigger a blink
     */
    triggerBlink() {
        this.animationState.blinking = true;
        this.animationState.blinkProgress = 0;
    }
    
    /**
     * Schedule random blinks
     */
    scheduleBlink() {
        const delay = 2000 + Math.random() * 4000; // 2-6 seconds
        this.blinkTimer = setTimeout(() => {
            this.triggerBlink();
            this.scheduleBlink();
        }, delay);
    }
    
    /**
     * Animation loop
     */
    startAnimationLoop() {
        const animate = () => {
            const state = this.animationState;
            const dt = 1 / 60;
            state.time += dt;
            
            // Breathing
            state.breathValue = Math.sin(state.time * 2) * 0.5 + 0.5;
            this.setParam(this.PARAMS.BREATH, state.breathValue);
            
            // Blink animation
            if (state.blinking) {
                state.blinkProgress += dt * 8;
                
                if (state.blinkProgress < 0.5) {
                    const closeValue = 1 - (state.blinkProgress * 2);
                    this.setParam(this.PARAMS.EYE_L_OPEN, closeValue);
                    this.setParam(this.PARAMS.EYE_R_OPEN, closeValue);
                } else if (state.blinkProgress < 1) {
                    const openValue = (state.blinkProgress - 0.5) * 2;
                    this.setParam(this.PARAMS.EYE_L_OPEN, openValue);
                    this.setParam(this.PARAMS.EYE_R_OPEN, openValue);
                } else {
                    state.blinking = false;
                    this.setParam(this.PARAMS.EYE_L_OPEN, 1);
                    this.setParam(this.PARAMS.EYE_R_OPEN, 1);
                }
            }
            
            // Idle head movement
            state.targetHeadX = Math.sin(state.time * 0.5) * 5;
            state.targetHeadY = Math.cos(state.time * 0.3) * 3;
            state.headTiltX += (state.targetHeadX - state.headTiltX) * 0.05;
            state.headTiltY += (state.targetHeadY - state.headTiltY) * 0.05;
            state.headTiltZ = Math.sin(state.time * 0.4) * 2;
            
            this.setParam(this.PARAMS.ANGLE_X, state.headTiltX);
            this.setParam(this.PARAMS.ANGLE_Y, state.headTiltY);
            this.setParam(this.PARAMS.ANGLE_Z, state.headTiltZ);
            
            // Body sway
            this.setParam(this.PARAMS.BODY_ANGLE_X, Math.sin(state.time * 0.3) * 2);
            this.setParam(this.PARAMS.BODY_ANGLE_Y, Math.cos(state.time * 0.2) * 1);
            
            // Eye following
            this.setParam(this.PARAMS.EYE_BALL_X, state.eyeLookX * 0.3);
            this.setParam(this.PARAMS.EYE_BALL_Y, state.eyeLookY * 0.3);
            
            this.animationFrame = requestAnimationFrame(animate);
        };
        
        this.animationFrame = requestAnimationFrame(animate);
    }
    
    /**
     * Set lip sync value (0-1) with optional vowel
     * @param {number} value - Mouth open amount (0-1)
     * @param {string} vowel - Optional vowel hint ('a', 'i', 'u', 'e', 'o')
     */
    // Replace entire setLipSync function with this optimized version
    setLipSync(value, vowel = null) {
        const intensity = Math.min(1, Math.max(0, value));
        const state = this.lipSyncState;

        console.log(`[Live2D] setLipSync: intensity=${intensity.toFixed(2)}, vowel=${vowel}`);

        // === Mouth openness: semi-binary (full when speaking) ===
        const targetOpen = intensity > 0.15 ? 1.0 : 0.0;  // threshold avoids micro-movements
        const smoothSpeed = 0.4;  // fast but natural (open & close same speed)
        state.mouthOpen += (targetOpen - state.mouthOpen) * smoothSpeed;

        this.setParam(this.PARAMS.MOUTH_OPEN_Y, state.mouthOpen * 1.0);  // multiplier 1.0

        // === Vowel shapes ===
        // Remember last detected vowel
        if (vowel && intensity > 0.15) {
            state.currentVowel = vowel.toLowerCase();
        }

        const speaking = state.mouthOpen > 0.2;
        const vowelSmooth = 0.5;  // fast shape changes
        const fadeSpeed = 0.85;   // quick fade when silent

        if (speaking && state.currentVowel) {
            // Drive only the active vowel to full strength
            state.vowelA += (state.currentVowel === 'a' ? state.mouthOpen : 0 - state.vowelA) * vowelSmooth;
            state.vowelI += (state.currentVowel === 'i' ? state.mouthOpen : 0 - state.vowelI) * vowelSmooth;
            state.vowelU += (state.currentVowel === 'u' ? state.mouthOpen : 0 - state.vowelU) * vowelSmooth;
            state.vowelE += (state.currentVowel === 'e' ? state.mouthOpen : 0 - state.vowelE) * vowelSmooth;
            state.vowelO += (state.currentVowel === 'o' ? state.mouthOpen : 0 - state.vowelO) * vowelSmooth;
        } else {
            // Fade all vowels when silent/low
            state.vowelA *= fadeSpeed;
            state.vowelI *= fadeSpeed;
            state.vowelU *= fadeSpeed;
            state.vowelE *= fadeSpeed;
            state.vowelO *= fadeSpeed;
        }

        // Apply with multiplier 1.0
        this.setParam(this.PARAMS.VOWEL_A, state.vowelA * 1.0);
        this.setParam(this.PARAMS.VOWEL_I, state.vowelI * 1.0);
        this.setParam(this.PARAMS.VOWEL_U, state.vowelU * 1.0);
        this.setParam(this.PARAMS.VOWEL_E, state.vowelE * 1.0);
        this.setParam(this.PARAMS.VOWEL_O, state.vowelO * 1.0);

        // Keep the slight mouth form variation when speaking
        if (intensity > 0.1) {
            this.setParam(this.PARAMS.MOUTH_FORM, 0.5 + Math.sin(Date.now() / 200) * 0.2);
        }
    }
    
    /**
     * Set lip sync with specific vowel shape
     * @param {string} vowel - Vowel ('a', 'i', 'u', 'e', 'o')
     * @param {number} intensity - Intensity (0-1)
     */
    setVowel(vowel, intensity = 1) {
        this.setLipSync(intensity, vowel);
    }
    
    /**
     * Show error message
     */
    showError(message) {
        const toast = document.getElementById('error-toast');
        toast.textContent = '⚠️ ' + message;
        toast.classList.remove('hidden');
        
        setTimeout(() => {
            toast.classList.add('hidden');
        }, 5000);
    }
    
    /**
     * Cleanup
     */
    destroy() {
        if (this.blinkTimer) {
            clearTimeout(this.blinkTimer);
        }
        if (this.animationFrame) {
            cancelAnimationFrame(this.animationFrame);
        }
        if (this.model) {
            this.model.destroy();
        }
        if (this.app) {
            this.app.destroy(true);
        }
    }
}

// Export for use in other scripts
window.Live2DAvatar = Live2DAvatar;
