
import unittest
import sys
from unittest.mock import MagicMock, patch

try:
    import paramiko
    PARAMIKO_INSTALLED = True
    SSHExceptionCls = paramiko.SSHException
    BadHostKeyExceptionCls = paramiko.BadHostKeyException
except ImportError:
    PARAMIKO_INSTALLED = False

    paramiko_mock = MagicMock()
    paramiko_mock.SSHClient = MagicMock
    paramiko_mock.AutoAddPolicy = MagicMock
    paramiko_mock.MissingHostKeyPolicy = MagicMock
    paramiko_mock.RSAKey = MagicMock
    paramiko_mock.Ed25519Key = MagicMock
    paramiko_mock.ECDSAKey = MagicMock
    paramiko_mock.DSSKey = MagicMock

    class SSHExceptionMock(Exception): pass
    class BadHostKeyExceptionMock(Exception):
        def __init__(self, hostname, key, expected_key):
            self.hostname = hostname
            self.key = key
            self.expected_key = expected_key

    paramiko_mock.SSHException = SSHExceptionMock
    paramiko_mock.BadHostKeyException = BadHostKeyExceptionMock

    SSHExceptionCls = SSHExceptionMock
    BadHostKeyExceptionCls = BadHostKeyExceptionMock

    sys.modules['paramiko'] = paramiko_mock

from ssh_manager import SSHManager, _humanize_age_seconds


class TestSSHManager(unittest.TestCase):
    def setUp(self):
        self.servers = [
            {"alias": "alpha", "host": "192.0.2.1"},
            {"alias": "beta", "host": "192.0.2.2"},
            {"alias": "gamma", "host": "192.0.2.3"},
        ]
        self.manager = SSHManager(self.servers)

    def test_get_server_aliases_returns_all_aliases(self):
        """Test that get_server_aliases returns a list of all defined aliases."""
        aliases = self.manager.get_server_aliases()
        self.assertEqual(aliases, ["alpha", "beta", "gamma"])

    def test_get_server_aliases_empty(self):
        """Test that get_server_aliases returns an empty list when initialized with no servers."""
        manager = SSHManager([])
        self.assertEqual(manager.get_server_aliases(), [])

    def test_get_server_aliases_none_values(self):
        """Test that get_server_aliases handles alias values that might be None."""
        servers = [
            {"alias": None, "host": "192.0.2.1"},
            {"alias": "valid_alias", "host": "192.0.2.2"}
        ]
        manager = SSHManager(servers)
        aliases = manager.get_server_aliases()
        self.assertEqual(aliases, [None, "valid_alias"])


    @patch('ssh_manager.paramiko.SSHClient')
    def test_get_ssh_client_bad_host_key(self, mock_ssh_client_class):
        """Test _get_ssh_client returns correct tuple on BadHostKeyException."""
        import base64
        import hashlib
        mock_client = MagicMock()
        mock_ssh_client_class.return_value = mock_client

        # We need to mock _connect_client to raise the exception,
        # but _connect_client doesn't catch it, it passes it up to _get_ssh_client

        # Configure _configure_host_keys to succeed
        self.manager._configure_host_keys = MagicMock(return_value=(True, "path", MagicMock()))

        mock_key = MagicMock()
        mock_key.asbytes.return_value = b"fake_key_bytes"
        exception = BadHostKeyExceptionCls("hostname", mock_key, "expected_key")

        self.manager._connect_client = MagicMock(side_effect=exception)

        client, msg, fingerprint = self.manager._get_ssh_client({"host": "192.0.2.10"})

        self.assertIsNone(client)
        self.assertEqual(msg, "Host key mismatch")

        expected_fp = "SHA256:" + base64.b64encode(hashlib.sha256(b"fake_key_bytes").digest()).decode('utf-8').replace('=', '')
        self.assertEqual(fingerprint, expected_fp)

    @patch('ssh_manager.paramiko.SSHClient')
    def test_get_ssh_client_ssh_exception_verification_failed(self, mock_ssh_client_class):
        """Test _get_ssh_client handles SSHException with host key verification failed."""
        mock_client = MagicMock()
        mock_ssh_client_class.return_value = mock_client

        mock_policy = MagicMock()
        mock_policy.fingerprint = "test_fingerprint"
        self.manager._configure_host_keys = MagicMock(return_value=(True, "path", mock_policy))

        exception = SSHExceptionCls("Host key verification failed for 192.0.2.10")
        self.manager._connect_client = MagicMock(side_effect=exception)

        client, msg, fingerprint = self.manager._get_ssh_client({"host": "192.0.2.10"})

        self.assertIsNone(client)
        self.assertEqual(msg, "Host key verification failed for 192.0.2.10")
        self.assertEqual(fingerprint, "test_fingerprint")

    @patch('ssh_manager.paramiko.SSHClient')
    def test_get_ssh_client_ssh_exception_other(self, mock_ssh_client_class):
        """Test _get_ssh_client handles generic SSHException."""
        mock_client = MagicMock()
        mock_ssh_client_class.return_value = mock_client

        self.manager._configure_host_keys = MagicMock(return_value=(True, "path", MagicMock()))

        exception = SSHExceptionCls("Connection timed out")
        self.manager._connect_client = MagicMock(side_effect=exception)

        client, msg, fingerprint = self.manager._get_ssh_client({"host": "192.0.2.10"})

        self.assertIsNone(client)
        self.assertEqual(msg, "Connection timed out")
        self.assertIsNone(fingerprint)


class TestHumanizeAgeSeconds(unittest.TestCase):
    def test_empty_strings(self):
        """Test with empty strings and missing values."""
        self.assertEqual(_humanize_age_seconds(""), "n/a")
        self.assertEqual(_humanize_age_seconds("   "), "n/a")
        self.assertEqual(_humanize_age_seconds(None), "n/a")

    def test_na_strings(self):
        """Test with 'n/a' and similar variants."""
        self.assertEqual(_humanize_age_seconds("n/a"), "n/a")
        self.assertEqual(_humanize_age_seconds("   n/a  "), "n/a")

    def test_below_60_seconds(self):
        """Test valid inputs below 60 seconds."""
        self.assertEqual(_humanize_age_seconds("45"), "45s")
        self.assertEqual(_humanize_age_seconds("45s"), "45s")
        self.assertEqual(_humanize_age_seconds("45.5"), "45s")

    def test_60_seconds_and_above(self):
        """Test exactly 60 seconds and inputs forming complete units."""
        self.assertEqual(_humanize_age_seconds("60"), "1m")
        self.assertEqual(_humanize_age_seconds("60s"), "1m")

    def test_multi_unit_values(self):
        """Test multiple units (e.g., hours and minutes)."""
        self.assertEqual(_humanize_age_seconds("3600"), "1h")
        self.assertEqual(_humanize_age_seconds("3660"), "1h 1m")
        self.assertEqual(_humanize_age_seconds("90000"), "1d 1h")
        self.assertEqual(_humanize_age_seconds("86400"), "1d")

    def test_trailing_s_and_spaces(self):
        """Test inputs with trailing 's' and leading/trailing spaces."""
        self.assertEqual(_humanize_age_seconds(" 3660s  "), "1h 1m")

    def test_invalid_strings(self):
        """Test non-numerical invalid strings return original."""
        self.assertEqual(_humanize_age_seconds("invalid"), "invalid")
        self.assertEqual(_humanize_age_seconds("10 days"), "10 day")

if __name__ == '__main__':
    unittest.main()
