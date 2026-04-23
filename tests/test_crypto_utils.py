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

if __name__ == '__main__':
    unittest.main()
