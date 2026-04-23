import unittest
import time
import sys
from unittest.mock import MagicMock, patch

# Mock dependencies that are not available in the environment to allow importing app_state
# We use a context manager-like approach to patch sys.modules only during import
mock_config_manager = MagicMock()
mock_models = MagicMock()
mock_ssh_manager = MagicMock()
mock_cryptography = MagicMock()

original_modules = sys.modules.copy()
sys.modules["config_manager"] = mock_config_manager
sys.modules["models"] = mock_models
sys.modules["ssh_manager"] = mock_ssh_manager
sys.modules["cryptography"] = mock_cryptography
sys.modules["cryptography.fernet"] = MagicMock()

try:
    from app_state import LoginRateLimiter
finally:
    # Restore original modules to avoid polluting global state
    for mod in ["config_manager", "models", "ssh_manager", "cryptography", "cryptography.fernet"]:
        if mod in original_modules:
            sys.modules[mod] = original_modules[mod]
        else:
            del sys.modules[mod]

class TestLoginRateLimiter(unittest.TestCase):
    def test_basic_limiting(self):
        limiter = LoginRateLimiter(max_attempts=3, window_seconds=10)
        self.assertTrue(limiter.is_allowed("user1"))
        self.assertTrue(limiter.is_allowed("user1"))
        self.assertTrue(limiter.is_allowed("user1"))
        # It should be blocked now
        self.assertFalse(limiter.is_allowed("user1"))

        # Other user should not be affected
        self.assertTrue(limiter.is_allowed("user2"))

    def test_window_expiration(self):
        # Using a very small window for testing expiration
        limiter = LoginRateLimiter(max_attempts=2, window_seconds=0.1)
        self.assertTrue(limiter.is_allowed("user1"))
        self.assertTrue(limiter.is_allowed("user1"))
        self.assertFalse(limiter.is_allowed("user1"))

        time.sleep(0.15)

        # After sleep, it should be allowed again
        self.assertTrue(limiter.is_allowed("user1"))

    def test_reset(self):
        limiter = LoginRateLimiter(max_attempts=2, window_seconds=60)
        self.assertTrue(limiter.is_allowed("user1"))
        self.assertTrue(limiter.is_allowed("user1"))
        self.assertFalse(limiter.is_allowed("user1"))

        limiter.reset("user1")
        self.assertTrue(limiter.is_allowed("user1"))


    @patch('time.time')
    def test_exact_window_boundary(self, mock_time):
        # Test that eviction happens exactly when `now - attempt[0] >= window_seconds`
        limiter = LoginRateLimiter(max_attempts=2, window_seconds=10)

        # t=0.0
        mock_time.return_value = 0.0
        self.assertTrue(limiter.is_allowed("user1"))
        self.assertTrue(limiter.is_allowed("user1"))

        # t=9.9 (limit reached, attempts not evicted)
        mock_time.return_value = 9.9
        self.assertFalse(limiter.is_allowed("user1"))

        # t=10.0 (exact boundary, attempts from t=0.0 are evicted)
        mock_time.return_value = 10.0
        self.assertTrue(limiter.is_allowed("user1"))

    @patch('time.time')
    def test_multiple_evictions(self, mock_time):
        limiter = LoginRateLimiter(max_attempts=3, window_seconds=10)

        mock_time.return_value = 0.0
        self.assertTrue(limiter.is_allowed("user1"))
        mock_time.return_value = 2.0
        self.assertTrue(limiter.is_allowed("user1"))
        mock_time.return_value = 4.0
        self.assertTrue(limiter.is_allowed("user1"))

        # Limit reached
        mock_time.return_value = 5.0
        self.assertFalse(limiter.is_allowed("user1"))

        # Move forward, evict first two attempts
        mock_time.return_value = 13.0
        self.assertTrue(limiter.is_allowed("user1"))
        self.assertTrue(limiter.is_allowed("user1"))
        self.assertFalse(limiter.is_allowed("user1"))

if __name__ == "__main__":
    unittest.main()
