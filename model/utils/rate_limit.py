"""
Rate Limiting module for brute force protection.

Usage:
    from .rate_limit import RateLimiter
    
    limiter = RateLimiter()
    
    # In login function:
    allowed, wait_time = limiter.check(ip_address)
    if not allowed:
        return HTTPError(f'Too many attempts. Retry in {int(wait_time)}s', 429)
    
    # On failed login:
    limiter.record_failure(ip_address)
    
    # On successful login:
    limiter.clear(ip_address)
"""

import time
from threading import Lock


class RateLimiter:
    """
    Simple in-memory rate limiter for brute force protection.
    
    For production with multiple workers, consider using Redis instead.
    """

    def __init__(self):
        self._attempts = {}  # {key: (failure_count, lockout_until)}
        self._lock = Lock()

        # Import configuration from centralized config
        from config import (RATE_LIMIT_ENABLED, RATE_LIMIT_MAX_ATTEMPTS,
                            RATE_LIMIT_LOCKOUT_SECONDS)
        self.max_attempts = RATE_LIMIT_MAX_ATTEMPTS
        self.lockout_duration = RATE_LIMIT_LOCKOUT_SECONDS
        self.enabled = RATE_LIMIT_ENABLED

    def check(self, key: str) -> tuple:
        """
        Check if the key is rate limited.
        
        Returns:
            tuple: (allowed: bool, wait_time: float)
                - allowed: True if request is allowed
                - wait_time: Seconds until lockout expires (0 if allowed)
        """
        if not self.enabled:
            return True, 0

        with self._lock:
            if key not in self._attempts:
                return True, 0

            failures, lockout_until = self._attempts[key]

            if lockout_until is None:
                return True, 0

            now = time.time()
            if now >= lockout_until:
                # Lockout expired, reset
                self._attempts[key] = (0, None)
                return True, 0

            # Still locked out
            return False, lockout_until - now

    def record_failure(self, key: str) -> None:
        """Record a failed attempt for the key."""
        if not self.enabled:
            return

        with self._lock:
            failures, lockout_until = self._attempts.get(key, (0, None))

            # If currently locked out, don't increment
            if lockout_until and time.time() < lockout_until:
                return

            failures += 1

            if failures >= self.max_attempts:
                # Start lockout
                lockout_until = time.time() + self.lockout_duration
            else:
                lockout_until = None

            self._attempts[key] = (failures, lockout_until)

    def clear(self, key: str) -> None:
        """Clear attempts for the key (call on successful login)."""
        with self._lock:
            if key in self._attempts:
                del self._attempts[key]

    def get_banned_ips(self) -> list:
        """
        Get all currently banned IPs with their remaining lockout time.
        
        Returns:
            list: List of dicts with 'ip' and 'remaining_seconds'
        """
        if not self.enabled:
            return []

        banned = []
        now = time.time()
        with self._lock:
            for key, (failures, lockout_until) in self._attempts.items():
                if lockout_until and now < lockout_until:
                    banned.append({
                        'ip': key,
                        'remaining_seconds': int(lockout_until - now),
                        'failure_count': failures
                    })
        return banned

    def unban(self, key: str) -> bool:
        """
        Manually unban a specific IP.
        
        Returns:
            bool: True if IP was found and unbanned, False otherwise
        """
        with self._lock:
            if key in self._attempts:
                del self._attempts[key]
                return True
            return False


# Global instance for use across the application
login_limiter = RateLimiter()
