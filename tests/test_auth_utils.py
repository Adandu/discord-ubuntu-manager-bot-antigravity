import unittest

from auth_utils import hash_password, is_password_hash, verify_password


class AuthUtilsTests(unittest.TestCase):
    def test_hash_password_creates_verifiable_hash(self):
        hashed = hash_password("super-secret")
        self.assertTrue(is_password_hash(hashed))
        self.assertTrue(verify_password("super-secret", hashed))
        self.assertFalse(verify_password("wrong", hashed))

    def test_verify_password_supports_legacy_plaintext(self):
        self.assertTrue(verify_password("legacy", "legacy"))
        self.assertFalse(verify_password("legacy", "different"))




    def test_verify_password_empty_inputs(self):
        self.assertFalse(verify_password("", "hash"))
        self.assertFalse(verify_password("cand", ""))
        self.assertFalse(verify_password(None, "hash"))
        self.assertFalse(verify_password("cand", None))
        self.assertFalse(verify_password("", ""))
        self.assertFalse(verify_password(None, None))

    def test_verify_password_malformed_hash(self):
        from auth_utils import PASSWORD_HASH_PREFIX
        # Missing parts
        self.assertFalse(verify_password("pass", f"{PASSWORD_HASH_PREFIX}$invalid"))
        # Invalid iterations
        self.assertFalse(verify_password("pass", f"{PASSWORD_HASH_PREFIX}$not_an_int$salt$digest"))
        # Invalid base64 salt
        self.assertFalse(verify_password("pass", f"{PASSWORD_HASH_PREFIX}$1000$salt$$digest"))
        # Invalid base64 digest
        self.assertFalse(verify_password("pass", f"{PASSWORD_HASH_PREFIX}$1000$c2FsdA==$dig$$est"))

if __name__ == "__main__":
    unittest.main()
