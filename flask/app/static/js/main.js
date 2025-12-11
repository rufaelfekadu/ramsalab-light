/**
 * Main JavaScript file for the audio data collection project
 * Handles general utilities, form validation, navigation, and mobile menu
 */

(function() {
    'use strict';

    // ============================================
    // MOBILE MENU TOGGLE
    // ============================================
    
    const menuToggle = document.getElementById('menuToggle');
    const mainNav = document.getElementById('mainNav');
    
    if (menuToggle && mainNav) {
        menuToggle.addEventListener('click', function() {
            mainNav.classList.toggle('header__nav--open');
            
            // Update aria-label for accessibility
            const isOpen = mainNav.classList.contains('header__nav--open');
            menuToggle.setAttribute('aria-expanded', isOpen);
            menuToggle.setAttribute('aria-label', isOpen ? 'إغلاق القائمة' : 'فتح القائمة');
        });
        
        // Close menu when clicking outside
        document.addEventListener('click', function(event) {
            if (!mainNav.contains(event.target) && !menuToggle.contains(event.target)) {
                mainNav.classList.remove('header__nav--open');
                menuToggle.setAttribute('aria-expanded', 'false');
            }
        });
    }

    // ============================================
    // FORM VALIDATION HELPERS
    // ============================================
    
    /**
     * Validate a form and show error messages
     * @param {HTMLFormElement} form - The form element to validate
     * @returns {boolean} - True if form is valid, false otherwise
     */
    function validateForm(form) {
        const requiredFields = form.querySelectorAll('[required]');
        let isValid = true;
        
        requiredFields.forEach(function(field) {
            if (!field.value.trim() && field.type !== 'checkbox' && field.type !== 'radio') {
                isValid = false;
                showFieldError(field, 'هذا الحقل مطلوب');
            } else if ((field.type === 'checkbox' || field.type === 'radio') && !field.checked) {
                // For checkboxes and radios, check if at least one in the group is checked
                const groupName = field.name;
                const groupFields = form.querySelectorAll(`[name="${groupName}"]`);
                const atLeastOneChecked = Array.from(groupFields).some(f => f.checked);
                
                if (!atLeastOneChecked) {
                    isValid = false;
                    showFieldError(field, 'يرجى اختيار خيار واحد على الأقل');
                }
            } else {
                clearFieldError(field);
            }
        });
        
        return isValid;
    }
    
    /**
     * Show error message for a field
     * @param {HTMLElement} field - The form field
     * @param {string} message - Error message
     */
    function showFieldError(field, message) {
        clearFieldError(field);
        
        field.classList.add('error');
        const errorElement = document.createElement('span');
        errorElement.className = 'form-error';
        errorElement.textContent = message;
        field.parentNode.appendChild(errorElement);
    }
    
    /**
     * Clear error message for a field
     * @param {HTMLElement} field - The form field
     */
    function clearFieldError(field) {
        field.classList.remove('error');
        const errorElement = field.parentNode.querySelector('.form-error');
        if (errorElement) {
            errorElement.remove();
        }
    }
    
    // Attach validation to all forms on the page
    document.addEventListener('DOMContentLoaded', function() {
        const forms = document.querySelectorAll('form[data-validate]');
        forms.forEach(function(form) {
            form.addEventListener('submit', function(e) {
                if (!validateForm(form)) {
                    e.preventDefault();
                    return false;
                }
            });
        });
    });

    // ============================================
    // SMOOTH SCROLLING
    // ============================================
    
    // Smooth scroll for anchor links
    document.querySelectorAll('a[href^="#"]').forEach(function(anchor) {
        anchor.addEventListener('click', function(e) {
            const href = this.getAttribute('href');
            if (href !== '#' && href.length > 1) {
                const target = document.querySelector(href);
                if (target) {
                    e.preventDefault();
                    target.scrollIntoView({
                        behavior: 'smooth',
                        block: 'start'
                    });
                }
            }
        });
    });

    // ============================================
    // UTILITY FUNCTIONS
    // ============================================
    
    /**
     * Debounce function to limit function calls
     * @param {Function} func - Function to debounce
     * @param {number} wait - Wait time in milliseconds
     * @returns {Function} - Debounced function
     */
    function debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = function() {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    }
    
    /**
     * Format file size for display
     * @param {number} bytes - File size in bytes
     * @returns {string} - Formatted file size
     */
    function formatFileSize(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
    }
    
    // Export utility functions to global scope if needed
    window.FormUtils = {
        validate: validateForm,
        showError: showFieldError,
        clearError: clearFieldError,
        debounce: debounce,
        formatFileSize: formatFileSize
    };

    // ============================================
    // ACCESSIBILITY ENHANCEMENTS
    // ============================================
    
    // Add keyboard navigation support for custom buttons
    document.addEventListener('DOMContentLoaded', function() {
        const customButtons = document.querySelectorAll('.btn[role="button"]');
        customButtons.forEach(function(button) {
            button.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    this.click();
                }
            });
        });
    });

})();
