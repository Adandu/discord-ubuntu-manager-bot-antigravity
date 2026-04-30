
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

from ssh_manager import SSHManager


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

if __name__ == "__main__":
    unittest.main()
