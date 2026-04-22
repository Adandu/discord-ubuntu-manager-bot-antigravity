import sys
import unittest
from unittest.mock import MagicMock, patch

# Using a context manager or a more localized mock to avoid global side effects if possible,
# but since _parse_bool is imported from models which has top-level pydantic usage,
# we still need something to handle the import.

class TestParseBool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Mock pydantic before importing models
        cls.pydantic_patcher = patch.dict(sys.modules, {"pydantic": MagicMock()})
        cls.pydantic_patcher.start()
        from models import _parse_bool
        cls._parse_bool = staticmethod(_parse_bool)

    @classmethod
    def tearDownClass(cls):
        cls.pydantic_patcher.stop()

    def test_parse_bool_with_boolean(self):
        self.assertTrue(self._parse_bool(True))
        self.assertFalse(self._parse_bool(False))

    def test_parse_bool_with_string_true(self):
        self.assertTrue(self._parse_bool("true"))
        self.assertTrue(self._parse_bool("TRUE"))
        self.assertTrue(self._parse_bool(" True "))
        self.assertTrue(self._parse_bool("truE"))

    def test_parse_bool_with_string_false(self):
        self.assertFalse(self._parse_bool("false"))
        self.assertFalse(self._parse_bool("FALSE"))
        self.assertFalse(self._parse_bool(" False "))
        self.assertFalse(self._parse_bool("not-true"))
        self.assertFalse(self._parse_bool(""))

    def test_parse_bool_with_integers(self):
        # bool(1) is True, bool(0) is False
        self.assertTrue(self._parse_bool(1))
        self.assertFalse(self._parse_bool(0))
        self.assertTrue(self._parse_bool(42))

    def test_parse_bool_with_none(self):
        self.assertFalse(self._parse_bool(None))

    def test_parse_bool_with_collections(self):
        self.assertTrue(self._parse_bool([1]))
        self.assertFalse(self._parse_bool([]))
        self.assertTrue(self._parse_bool({"key": "value"}))
        self.assertFalse(self._parse_bool({}))

if __name__ == "__main__":
    unittest.main()
