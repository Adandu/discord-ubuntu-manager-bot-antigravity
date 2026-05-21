import unittest
from crypto_utils import CryptoManager

class TestCryptoManager(unittest.TestCase):
    def setUp(self):
        self.valid_key = "a" * 32
        self.cm = CryptoManager(self.valid_key)

    def test_encrypt_empty_or_none(self):
        self.assertEqual(self.cm.encrypt(""), "")
        self.assertEqual(self.cm.encrypt(None), "")

    def test_encrypt_already_encrypted(self):
        self.assertEqual(self.cm.encrypt("ENC:already_encrypted"), "ENC:already_encrypted")

    def test_encrypt_valid_string(self):
        plaintext = "hello world"
        encrypted = self.cm.encrypt(plaintext)

        self.assertTrue(encrypted.startswith("ENC:"))
        self.assertNotEqual(encrypted, plaintext)

        # Verify it can be decrypted back
        decrypted = self.cm.decrypt(encrypted)
        self.assertEqual(decrypted, plaintext)


    def test_decrypt_empty_or_none(self):
        self.assertEqual(self.cm.decrypt(""), "")
        self.assertEqual(self.cm.decrypt(None), None)

    def test_decrypt_not_encrypted(self):
        self.assertEqual(self.cm.decrypt("plaintext"), "plaintext")


    def test_decrypt_invalid_token_fails_both(self):
        # A token that is invalid for both current and legacy Fernet instances
        invalid_encrypted_string = "ENC:invalidtoken"

        # Should return the original string rather than throwing an exception
        decrypted = self.cm.decrypt(invalid_encrypted_string)
        self.assertEqual(decrypted, invalid_encrypted_string)

    def test_decrypt_outer_exception(self):
        # A string that fails .encode().decode() or base64 decoding entirely, triggering the outer exception.
        # Specifically, Fernet.decrypt throws TypeError or binascii.Error for non-base64 input,
        # but in this case InvalidToken handles it. Let's mock Fernet to raise a different Exception.
        from unittest.mock import patch

        with patch.object(self.cm.fernet, 'decrypt', side_effect=Exception("Test error")):
            decrypted = self.cm.decrypt("ENC:anything")
            self.assertEqual(decrypted, "ENC:anything")

if __name__ == '__main__':
    unittest.main()
