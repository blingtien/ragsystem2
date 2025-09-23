"""
Error Handling Module for Multimodal Processing
Provides centralized error categorization and recovery strategies
"""

import logging
from enum import Enum
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)

class ErrorCategory(Enum):
    """Error categories for multimodal operations"""
    VALIDATION = "validation_error"
    PROCESSING = "processing_error"
    NETWORK = "network_error"
    RESOURCE = "resource_error"
    UNKNOWN = "unknown_error"

class ErrorInfo:
    """Error information container with recovery context"""

    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        details: Optional[Dict] = None,
        is_recoverable: bool = False
    ):
        self.category = category
        self.message = message
        self.details = details or {}
        self.is_recoverable = is_recoverable
        self.user_message = self._generate_user_message()

    def _generate_user_message(self) -> str:
        """Generate user-friendly error message"""
        messages = {
            ErrorCategory.VALIDATION: "Invalid input provided. Please check your data format.",
            ErrorCategory.PROCESSING: "An error occurred while processing your request. Please try again.",
            ErrorCategory.NETWORK: "Network connection issue. Please check your connection.",
            ErrorCategory.RESOURCE: "System resources temporarily unavailable. Please try again later.",
            ErrorCategory.UNKNOWN: "An unexpected error occurred. Please contact support if the issue persists."
        }
        return messages.get(self.category, messages[ErrorCategory.UNKNOWN])

class MultimodalErrorHandler:
    """Centralized error handler for multimodal operations"""

    def __init__(self):
        self.error_counts = {category: 0 for category in ErrorCategory}
        self.recovery_strategies = {
            ErrorCategory.VALIDATION: self._validation_recovery,
            ErrorCategory.PROCESSING: self._processing_recovery,
            ErrorCategory.NETWORK: self._network_recovery,
            ErrorCategory.RESOURCE: self._resource_recovery,
            ErrorCategory.UNKNOWN: self._unknown_recovery
        }

    def handle_error(self, error: Exception, context: Dict = None) -> ErrorInfo:
        """Categorize and handle errors"""
        error_message = str(error)
        error_type = type(error).__name__

        # Categorize error
        category = self._categorize_error(error, error_message)

        # Track error occurrence
        self.error_counts[category] += 1

        # Determine if recoverable
        is_recoverable = self._is_recoverable(category, error)

        # Log error
        logger.error(
            f"Error in multimodal processing: category={category.value}, "
            f"type={error_type}, message={error_message}, "
            f"recoverable={is_recoverable}"
        )

        return ErrorInfo(
            category=category,
            message=error_message,
            details={
                'error_type': error_type,
                'context': context,
                'error_count': self.error_counts[category]
            },
            is_recoverable=is_recoverable
        )

    def _categorize_error(self, error: Exception, error_message: str) -> ErrorCategory:
        """Categorize error based on type and message"""

        # Check for specific exception types
        if hasattr(error, '__module__'):
            module = error.__module__
            if 'validation' in module.lower():
                return ErrorCategory.VALIDATION
            elif 'network' in module.lower() or 'connection' in module.lower():
                return ErrorCategory.NETWORK

        # Check error message patterns
        error_msg_lower = error_message.lower()

        if any(term in error_msg_lower for term in ['validation', 'invalid', 'format']):
            return ErrorCategory.VALIDATION
        elif any(term in error_msg_lower for term in ['timeout', 'connection', 'network']):
            return ErrorCategory.NETWORK
        elif any(term in error_msg_lower for term in ['memory', 'resource', 'space']):
            return ErrorCategory.RESOURCE
        elif any(term in error_msg_lower for term in ['process', 'parse', 'extract']):
            return ErrorCategory.PROCESSING
        else:
            return ErrorCategory.UNKNOWN

    def _is_recoverable(self, category: ErrorCategory, error: Exception) -> bool:
        """Determine if error is recoverable"""
        recoverable_categories = {
            ErrorCategory.VALIDATION: True,  # User can fix input
            ErrorCategory.NETWORK: True,     # Can retry
            ErrorCategory.RESOURCE: True,    # Can wait and retry
            ErrorCategory.PROCESSING: False, # Usually requires fix
            ErrorCategory.UNKNOWN: False     # Can't determine
        }

        # Override for specific error types
        if isinstance(error, MemoryError):
            return False  # Critical resource issue
        elif isinstance(error, KeyboardInterrupt):
            return False  # User initiated

        return recoverable_categories.get(category, False)

    def _validation_recovery(self, error_info: ErrorInfo) -> Dict[str, Any]:
        """Recovery strategy for validation errors"""
        return {
            'action': 'reject_input',
            'user_guidance': 'Please check your input format and try again',
            'suggestions': [
                'Ensure images are in JPEG, PNG, or WebP format',
                'Check that tables have consistent column counts',
                'Verify LaTeX equation syntax'
            ]
        }

    def _processing_recovery(self, error_info: ErrorInfo) -> Dict[str, Any]:
        """Recovery strategy for processing errors"""
        return {
            'action': 'log_and_skip',
            'fallback': 'process_without_enhancement',
            'user_guidance': 'Some processing features may be limited'
        }

    def _network_recovery(self, error_info: ErrorInfo) -> Dict[str, Any]:
        """Recovery strategy for network errors"""
        return {
            'action': 'retry_with_backoff',
            'max_retries': 3,
            'backoff_factor': 2,
            'user_guidance': 'Retrying connection...'
        }

    def _resource_recovery(self, error_info: ErrorInfo) -> Dict[str, Any]:
        """Recovery strategy for resource errors"""
        return {
            'action': 'reduce_load',
            'strategies': [
                'reduce_batch_size',
                'enable_lazy_loading',
                'clear_cache',
                'defer_non_critical'
            ],
            'user_guidance': 'Optimizing resource usage...'
        }

    def _unknown_recovery(self, error_info: ErrorInfo) -> Dict[str, Any]:
        """Recovery strategy for unknown errors"""
        return {
            'action': 'fail_safe',
            'log_level': 'critical',
            'user_guidance': 'An unexpected error occurred. Support has been notified.'
        }

    def get_recovery_strategy(self, category: ErrorCategory) -> Dict[str, Any]:
        """Get recovery strategy for error category"""
        strategy_func = self.recovery_strategies.get(
            category,
            self._unknown_recovery
        )
        return strategy_func(None)

    def reset_error_counts(self):
        """Reset error tracking counters"""
        self.error_counts = {category: 0 for category in ErrorCategory}