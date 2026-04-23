import unittest
from unittest.mock import MagicMock, patch
from ssh_manager import SSHManager

class TestSSHManagerLogic(unittest.TestCase):
    def setUp(self):
        self.servers = [
            {"alias": "server1", "host": "1.1.1.1", "user": "user1", "password": "pass1"},
            {"alias": "root_server", "host": "2.2.2.2", "user": "root", "password": "root_pass"}
        ]
        self.manager = SSHManager(self.servers)

    def test_prepare_command_non_root_sudo(self):
        config = self.servers[0]
        command = "sudo apt update"
        prepared_cmd, password = self.manager._prepare_command(command, config)

        self.assertEqual(password, "pass1")
        self.assertEqual(prepared_cmd, 'sudo -S -E env PATH="$PATH:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" apt update')

    def test_prepare_command_non_root_no_sudo(self):
        config = self.servers[0]
        command = "ls -la"
        prepared_cmd, password = self.manager._prepare_command(command, config)

        self.assertIsNone(password)
        self.assertEqual(prepared_cmd, 'env PATH="$PATH:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" ls -la')

    def test_prepare_command_root(self):
        config = self.servers[1]
        command = "sudo apt update"
        prepared_cmd, password = self.manager._prepare_command(command, config)

        self.assertIsNone(password)
        self.assertEqual(prepared_cmd, 'env PATH="$PATH:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" sudo apt update')

    @patch('paramiko.SSHClient')
    def test_execute_internal_success(self, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b"success output"
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""
        mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)

        config = self.servers[0]
        result = self.manager._execute_internal(mock_client, config, "ls", "server1")

        self.assertEqual(result, "success output")
        mock_client.exec_command.assert_called_once()
        args, _ = mock_client.exec_command.call_args
        self.assertIn("env PATH=", args[0])
        self.assertIn("ls", args[0])

    @patch('paramiko.SSHClient')
    def test_execute_internal_with_error(self, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b""
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b"some error"
        mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)

        config = self.servers[0]
        result = self.manager._execute_internal(mock_client, config, "ls", "server1")

        self.assertEqual(result, "some error")

    @patch('paramiko.SSHClient')
    def test_execute_internal_sudo_password_cleaning(self, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b"output"
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b"[sudo] password for user1: \nactual error"
        mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)

        config = self.servers[0]
        result = self.manager._execute_internal(mock_client, config, "sudo ls", "server1")

        self.assertIn("output", result)
        self.assertIn("actual error", result)
        self.assertNotIn("[sudo] password for", result)

if __name__ == "__main__":
    unittest.main()
