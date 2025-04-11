from functools import wraps
import asyncio
import logging
from typing import Callable, Type, Union, Tuple
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryError
)

logger = logging.getLogger(__name__)

# Common exceptions to retry on
RETRYABLE_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    asyncio.TimeoutError,
)

def create_retry_decorator(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 10.0,
    exceptions: Union[Type[Exception], Tuple[Type[Exception], ...]] = RETRYABLE_EXCEPTIONS
) -> Callable:
    """
    Create a retry decorator with exponential backoff.
    
    Args:
        max_attempts: Maximum number of retry attempts
        initial_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries in seconds
        exceptions: Exception types to retry on
        
    Returns:
        A decorator that can be applied to async functions
    """
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=initial_delay, max=max_delay),
        retry=retry_if_exception_type(exceptions),
        reraise=True
    )

def with_retry(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 10.0,
    exceptions: Union[Type[Exception], Tuple[Type[Exception], ...]] = RETRYABLE_EXCEPTIONS
) -> Callable:
    """
    Decorator factory for retrying async functions with exponential backoff.
    
    Example:
        @with_retry(max_attempts=3)
        async def my_function():
            # Your code here
            pass
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            retry_decorator = create_retry_decorator(
                max_attempts=max_attempts,
                initial_delay=initial_delay,
                max_delay=max_delay,
                exceptions=exceptions
            )
            
            try:
                return await retry_decorator(func)(*args, **kwargs)
            except RetryError as e:
                logger.error(f"All retry attempts failed for {func.__name__}: {str(e)}")
                raise
            except Exception as e:
                logger.error(f"Unexpected error in {func.__name__}: {str(e)}")
                raise
                
        return wrapper
    return decorator

class RetryableError(Exception):
    """Base class for retryable errors."""
    pass

class TransientError(RetryableError):
    """Error that might be resolved by retrying."""
    pass

class PermanentError(RetryableError):
    """Error that won't be resolved by retrying."""
    pass 