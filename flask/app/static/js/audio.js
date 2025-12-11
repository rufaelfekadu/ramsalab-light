/**
 * Audio Recording and Upload Module
 * Handles MediaRecorder API, audio recording, and file uploads
 */

(function() {
    'use strict';

    /**
     * AudioRecorder class for handling audio recording and upload
     */
    class AudioRecorder {
        constructor(options) {
            this.startBtn = options.startBtn;
            this.stopBtn = options.stopBtn;
            this.uploadInput = options.uploadInput;
            this.statusDisplay = options.statusDisplay;
            this.recordingIndicator = options.recordingIndicator;
            this.questionId = options.questionId || null;
            this.surveyId = options.surveyId || null;
            this.submitBtn = options.submitBtn || null;
            this.submitForm = options.submitForm || null;
            this.csrfToken = options.csrfToken || null;
            
            this.mediaRecorder = null;
            this.audioChunks = [];
            this.isRecording = false;
            this.stream = null;
            this.recordedAudioBlob = null;
            this.isUploading = false;
            this.permissionModal = document.getElementById('permissionModal');
            this.allowPermissionBtn = document.getElementById('allowPermissionBtn');
            this.tryAgainBtn = document.getElementById('tryAgainBtn');
            this.closeModalBtn = document.getElementById('closeModalBtn');
            this.audioPreview = document.getElementById('audioPreview');
            this.audioPreviewPlayer = document.getElementById('audioPreviewPlayer');
            this.recordAgainBtn = document.getElementById('recordAgainBtn');
            this.confirmDeleteModal = document.getElementById('confirmDeleteModal');
            this.confirmDeleteBtn = document.getElementById('confirmDeleteBtn');
            this.cancelDeleteBtn = document.getElementById('cancelDeleteBtn');
            
            // Status messages in Arabic
            this.statusMessages = {
                idle: 'لا يوجد تسجيل حاليًا',
                requesting: 'جاري طلب إذن الميكروفون...',
                recording: 'جاري التسجيل...',
                stopping: 'جاري إيقاف التسجيل...',
                uploading: 'جاري رفع الملف...',
                success: 'تم رفع الملف بنجاح',
                error: 'حدث خطأ. يرجى المحاولة مرة أخرى',
                noPermission: 'لم يتم منح إذن استخدام الميكروفون',
                noSupport: 'المتصفح لا يدعم التسجيل الصوتي'
            };
            
            this.init();
        }
        
        /**
         * Initialize event listeners
         */
        init() {
            if (!this.startBtn || !this.stopBtn) {
                console.error('AudioRecorder: Start and stop buttons are required');
                return;
            }
            
            // Check browser support
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                this.updateStatus(this.statusMessages.noSupport, 'error');
                this.disableControls();
                return;
            }
            
            // Attach event listeners
            this.startBtn.addEventListener('click', () => this.showPermissionModal());
            this.stopBtn.addEventListener('click', () => this.stopRecording());
            
            if (this.uploadInput) {
                this.uploadInput.addEventListener('change', (e) => this.handleFileUpload(e));
            }
            
            // Setup permission modal event listeners
            if (this.allowPermissionBtn) {
                this.allowPermissionBtn.addEventListener('click', () => {
                    this.hidePermissionModal();
                    this.startRecording();
                });
            }
            
            // Keep tryAgainBtn for backward compatibility (if it exists)
            if (this.tryAgainBtn) {
                this.tryAgainBtn.addEventListener('click', () => {
                    this.hidePermissionModal();
                    this.startRecording();
                });
            }
            
            if (this.closeModalBtn) {
                this.closeModalBtn.addEventListener('click', () => {
                    this.hidePermissionModal();
                });
            }
            
            // Close modal when clicking outside
            if (this.permissionModal) {
                this.permissionModal.addEventListener('click', (e) => {
                    if (e.target === this.permissionModal) {
                        this.hidePermissionModal();
                    }
                });
            }
            
            // Setup record again button
            if (this.recordAgainBtn) {
                this.recordAgainBtn.addEventListener('click', () => {
                    this.showConfirmDeleteModal();
                });
            }
            
            // Setup confirmation modal buttons
            if (this.confirmDeleteBtn) {
                this.confirmDeleteBtn.addEventListener('click', () => {
                    this.hideConfirmDeleteModal();
                    this.hideAudioPreview();
                    this.resetRecording();
                });
            }
            
            if (this.cancelDeleteBtn) {
                this.cancelDeleteBtn.addEventListener('click', () => {
                    this.hideConfirmDeleteModal();
                });
            }
            
            // Close confirmation modal when clicking outside
            if (this.confirmDeleteModal) {
                this.confirmDeleteModal.addEventListener('click', (e) => {
                    if (e.target === this.confirmDeleteModal) {
                        this.hideConfirmDeleteModal();
                    }
                });
            }
            
            this.updateStatus(this.statusMessages.idle, 'idle');
        }
        
        /**
         * Start audio recording
         */
        async startRecording() {
            try {
                this.updateStatus(this.statusMessages.requesting, 'idle');
                
                // Request microphone access
                this.stream = await navigator.mediaDevices.getUserMedia({ 
                    audio: {
                        echoCancellation: true,
                        noiseSuppression: true,
                        autoGainControl: true
                    } 
                });
                
                // Determine MIME type based on browser support
                let mimeType = 'audio/webm';
                if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
                    mimeType = 'audio/webm;codecs=opus';
                } else if (MediaRecorder.isTypeSupported('audio/webm')) {
                    mimeType = 'audio/webm';
                } else if (MediaRecorder.isTypeSupported('audio/mp4')) {
                    mimeType = 'audio/mp4';
                } else if (MediaRecorder.isTypeSupported('audio/ogg')) {
                    mimeType = 'audio/ogg';
                }
                
                // Create MediaRecorder
                this.mediaRecorder = new MediaRecorder(this.stream, {
                    mimeType: mimeType
                });
                
                this.audioChunks = [];
                
                // Handle data available event
                this.mediaRecorder.ondataavailable = (event) => {
                    if (event.data.size > 0) {
                        this.audioChunks.push(event.data);
                    }
                };
                
                // Handle recording stop
                this.mediaRecorder.onstop = () => {
                    this.handleRecordingComplete();
                };
                
                // Handle errors
                this.mediaRecorder.onerror = (event) => {
                    console.error('MediaRecorder error:', event);
                    this.updateStatus(this.statusMessages.error, 'error');
                    this.resetRecording();
                };
                
                // Start recording
                this.mediaRecorder.start(1000); // Collect data every second
                this.isRecording = true;
                
                // Update UI
                this.startBtn.disabled = true;
                this.startBtn.style.display = 'none';
                this.stopBtn.disabled = false;
                this.stopBtn.style.display = 'inline-flex';
                this.updateStatus(this.statusMessages.recording, 'recording');
                
                if (this.recordingIndicator) {
                    this.recordingIndicator.classList.add('recording-indicator--active');
                }
                
            } catch (error) {
                console.error('Error starting recording:', error);
                
                if (error.name === 'NotAllowedError' || error.name === 'PermissionDeniedError') {
                    this.updateStatus(this.statusMessages.noPermission, 'error');
                    this.showPermissionModal();
                } else {
                    this.updateStatus(this.statusMessages.error, 'error');
                }
                
                this.resetRecording();
            }
        }
        
        /**
         * Stop audio recording
         */
        stopRecording() {
            if (!this.mediaRecorder || !this.isRecording) {
                return;
            }
            
            this.updateStatus(this.statusMessages.stopping, 'idle');
            
            // Stop MediaRecorder
            this.mediaRecorder.stop();
            
            // Stop all tracks in the stream
            if (this.stream) {
                this.stream.getTracks().forEach(track => track.stop());
            }
            
            this.isRecording = false;
            
            // Update UI
            this.startBtn.disabled = false;
            this.startBtn.style.display = 'inline-flex';
            this.stopBtn.disabled = true;
            this.stopBtn.style.display = 'none';
            
            if (this.recordingIndicator) {
                this.recordingIndicator.classList.remove('recording-indicator--active');
            }
        }
        
        /**
         * Handle recording completion and upload
         */
        async handleRecordingComplete() {
            if (this.audioChunks.length === 0) {
                this.updateStatus(this.statusMessages.error, 'error');
                this.resetRecording();
                return;
            }
            
            // Create audio blob
            const mimeType = this.mediaRecorder.mimeType || 'audio/webm';
            const audioBlob = new Blob(this.audioChunks, { type: mimeType });
            
            // Store the recorded audio blob
            this.recordedAudioBlob = audioBlob;
            
            // Show audio preview
            this.showAudioPreview(audioBlob);
            
            // Enable submit button if available
            if (this.submitBtn) {
                this.submitBtn.disabled = false;
            }
            
            // Update status
            this.updateStatus('تم التسجيل بنجاح. استمع إلى المعاينة ثم اضغط على "إرسال" لإرسال التسجيل.', 'success');
            
            // Reset recording state (but keep the blob)
            this.isRecording = false;
            this.audioChunks = [];
            
            if (this.stream) {
                this.stream.getTracks().forEach(track => track.stop());
                this.stream = null;
            }
            
            this.startBtn.disabled = false;
            this.startBtn.style.display = 'inline-flex';
            this.stopBtn.disabled = true;
            this.stopBtn.style.display = 'none';
            
            if (this.recordingIndicator) {
                this.recordingIndicator.classList.remove('recording-indicator--active');
            }
        }
        
        /**
         * Handle file upload from input
         */
        async handleFileUpload(event) {
            const file = event.target.files[0];
            
            if (!file) {
                return;
            }
            
            // Validate file type
            const allowedTypes = ['audio/wav', 'audio/mp3', 'audio/webm', 'audio/ogg', 'audio/mpeg'];
            const fileExtension = file.name.split('.').pop().toLowerCase();
            const allowedExtensions = ['wav', 'mp3', 'webm', 'ogg', 'm4a'];
            
            if (!allowedTypes.includes(file.type) && !allowedExtensions.includes(fileExtension)) {
                this.updateStatus('نوع الملف غير مدعوم. يرجى اختيار ملف صوتي (wav, mp3, webm)', 'error');
                event.target.value = ''; // Clear input
                return;
            }
            
            // Validate file size (max 16MB)
            const maxSize = 16 * 1024 * 1024; // 16MB
            if (file.size > maxSize) {
                this.updateStatus('حجم الملف كبير جداً. الحد الأقصى 16 ميجابايت', 'error');
                event.target.value = ''; // Clear input
                return;
            }
            
            // Store the file
            this.recordedAudioBlob = file;
            
            // Show audio preview
            this.showAudioPreview(file);
            
            // Enable submit button if available
            if (this.submitBtn) {
                this.submitBtn.disabled = false;
            }
            
            // Update status
            this.updateStatus('تم اختيار الملف بنجاح. استمع إلى المعاينة ثم اضغط على "إرسال" لإرسال الملف.', 'success');
        }
        
        /**
         * Submit the recorded/uploaded audio
         */
        async submitAudio() {
            // Prevent multiple simultaneous uploads
            if (this.isUploading) {
                this.updateStatus('جاري رفع الملف... يرجى الانتظار.', 'idle');
                return;
            }
            
            if (!this.recordedAudioBlob) {
                this.updateStatus('لا يوجد ملف صوتي للإرسال.', 'error');
                return;
            }
            
            // Disable submit button immediately to prevent multiple clicks
            if (this.submitBtn) {
                this.submitBtn.disabled = true;
            }
            
            this.isUploading = true;
            
            try {
                await this.uploadAudio(this.recordedAudioBlob, 'recording');
            } catch (error) {
                // Re-enable button on error
                if (this.submitBtn) {
                    this.submitBtn.disabled = false;
                }
                this.isUploading = false;
                throw error;
            }
        }
        
        /**
         * Upload audio file to server
         */
        async uploadAudio(audioBlob, source) {
            try {
                this.updateStatus(this.statusMessages.uploading, 'idle');
                
                // Create FormData
                const formData = new FormData();
                
                // Ensure filename has proper extension
                const extension = this.getFileExtension(audioBlob);
                const filename = `recording_${Date.now()}.${extension}`;
                formData.append('audio', audioBlob, filename);
                
                // Add CSRF token if available
                if (this.csrfToken) {
                    formData.append('csrf_token', this.csrfToken);
                }
                
                // Add question_id and survey_id if available
                if (this.questionId) {
                    formData.append('question_id', this.questionId);
                }
                if (this.surveyId) {
                    formData.append('survey_id', this.surveyId);
                }
                
                // Upload to server via AJAX
                const response = await fetch('/submit_audio', {
                    method: 'POST',
                    headers: {
                        'X-Requested-With': 'XMLHttpRequest'
                    },
                    body: formData,
                    credentials: 'same-origin'
                });
                
                // Check if response is ok before trying to parse JSON
                if (!response.ok) {
                    // Try to get error message from response
                    let errorMessage = `Server error: ${response.status}`;
                    try {
                        const errorResult = await response.json();
                        errorMessage = errorResult.message || errorMessage;
                    } catch (e) {
                        // If response is not JSON, use status text
                        errorMessage = response.statusText || errorMessage;
                    }
                    throw new Error(errorMessage);
                }
                
                const result = await response.json();
                
                if (result.status === 'success') {
                    this.updateStatus(this.statusMessages.success, 'success');
                    
                    // Clear the recorded audio blob and disable submit button to prevent duplicate uploads
                    this.recordedAudioBlob = null;
                    if (this.submitBtn) {
                        this.submitBtn.disabled = true;
                    }
                    this.isUploading = false;
                    
                    // Hide audio preview after successful upload
                    this.hideAudioPreview();
                    
                    // Redirect to thanks page if redirect URL is provided
                    if (result.redirect) {
                        setTimeout(() => {
                            window.location.href = result.redirect;
                        }, 1000);
                    } else {
                        // Fallback: redirect to thanks page
                        setTimeout(() => {
                            window.location.href = '/thanks';
                        }, 1000);
                    }
                } else {
                    // Re-enable button on failure
                    if (this.submitBtn) {
                        this.submitBtn.disabled = false;
                    }
                    this.isUploading = false;
                    throw new Error(result.message || 'Upload failed');
                }
                
            } catch (error) {
                console.error('Upload error:', error);
                const errorMessage = error.message || this.statusMessages.error;
                this.updateStatus(errorMessage, 'error');
                
                // Re-enable button and reset upload state on error
                if (this.submitBtn) {
                    this.submitBtn.disabled = false;
                }
                this.isUploading = false;
                
                // Show error in console for debugging
                console.error('Full error details:', {
                    message: error.message,
                    stack: error.stack
                });
            }
        }
        
        /**
         * Get file extension from blob or file
         */
        getFileExtension(file) {
            if (file.name) {
                return file.name.split('.').pop();
            }
            
            // Determine from MIME type
            const mimeType = file.type || 'audio/webm';
            if (mimeType.includes('webm')) return 'webm';
            if (mimeType.includes('mp4') || mimeType.includes('m4a')) return 'm4a';
            if (mimeType.includes('ogg')) return 'ogg';
            if (mimeType.includes('wav')) return 'wav';
            if (mimeType.includes('mp3') || mimeType.includes('mpeg')) return 'mp3';
            
            return 'webm'; // Default
        }
        
        /**
         * Update status display
         */
        updateStatus(message, type) {
            if (!this.statusDisplay) {
                return;
            }
            
            this.statusDisplay.textContent = message;
            
            // Remove all status classes
            this.statusDisplay.classList.remove(
                'status-display--idle',
                'status-display--recording',
                'status-display--success'
            );
            
            // Add appropriate class
            if (type === 'recording') {
                this.statusDisplay.classList.add('status-display--recording');
            } else if (type === 'success') {
                this.statusDisplay.classList.add('status-display--success');
            } else {
                this.statusDisplay.classList.add('status-display--idle');
            }
        }
        
        /**
         * Reset recording state
         */
        resetRecording() {
            this.isRecording = false;
            this.audioChunks = [];
            this.recordedAudioBlob = null;
            this.isUploading = false;
            
            if (this.stream) {
                this.stream.getTracks().forEach(track => track.stop());
                this.stream = null;
            }
            
            this.startBtn.disabled = false;
            this.startBtn.style.display = 'inline-flex';
            this.stopBtn.disabled = true;
            this.stopBtn.style.display = 'none';
            
            if (this.recordingIndicator) {
                this.recordingIndicator.classList.remove('recording-indicator--active');
            }
            
            if (this.submitBtn) {
                this.submitBtn.disabled = true;
            }
            
            // Hide audio preview when resetting
            this.hideAudioPreview();
        }
        
        /**
         * Disable all controls (when browser doesn't support recording)
         */
        disableControls() {
            if (this.startBtn) this.startBtn.disabled = true;
            if (this.stopBtn) this.stopBtn.disabled = true;
        }
        
        /**
         * Show permission modal
         */
        showPermissionModal() {
            if (this.permissionModal) {
                this.permissionModal.classList.add('permission-modal--active');
            }
        }
        
        /**
         * Hide permission modal
         */
        hidePermissionModal() {
            if (this.permissionModal) {
                this.permissionModal.classList.remove('permission-modal--active');
            }
        }
        
        /**
         * Show confirmation modal for deleting recording
         */
        showConfirmDeleteModal() {
            if (this.confirmDeleteModal) {
                this.confirmDeleteModal.classList.add('permission-modal--active');
            }
        }
        
        /**
         * Hide confirmation modal for deleting recording
         */
        hideConfirmDeleteModal() {
            if (this.confirmDeleteModal) {
                this.confirmDeleteModal.classList.remove('permission-modal--active');
            }
        }
        
        /**
         * Show audio preview
         */
        showAudioPreview(audioBlob) {
            if (this.audioPreview && this.audioPreviewPlayer) {
                // Create object URL for the audio blob
                const audioUrl = URL.createObjectURL(audioBlob);
                this.audioPreviewPlayer.src = audioUrl;
                this.audioPreview.classList.add('audio-preview--active');
            }
        }
        
        /**
         * Hide audio preview
         */
        hideAudioPreview() {
            if (this.audioPreview && this.audioPreviewPlayer) {
                // Revoke object URL to free memory
                if (this.audioPreviewPlayer.src) {
                    URL.revokeObjectURL(this.audioPreviewPlayer.src);
                }
                this.audioPreviewPlayer.src = '';
                this.audioPreview.classList.remove('audio-preview--active');
            }
        }
    }
    
    // Export to global scope
    window.AudioRecorder = AudioRecorder;
    
})();
