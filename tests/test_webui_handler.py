import unittest
import logging
from collections import deque
from app_state import WebUIHandler

class TestWebUIHandler(unittest.TestCase):
    def setUp(self):
        self.log_buffer = deque(maxlen=3)
        self.handler = WebUIHandler(self.log_buffer)
        formatter = logging.Formatter('%(levelname)s:%(message)s')
        self.handler.setFormatter(formatter)

    def test_emit_adds_to_buffer(self):
        record = logging.LogRecord(
            name="test_logger", level=logging.INFO, pathname="", lineno=0,
            msg="Test message", args=(), exc_info=None
        )
        self.handler.emit(record)

        self.assertEqual(len(self.log_buffer), 1)
        self.assertEqual(self.log_buffer[0], "INFO:Test message")

    def test_emit_respects_maxlen(self):
        records = [
            logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=0,
                msg=f"Msg {i}", args=(), exc_info=None
            )
            for i in range(5)
        ]

        for record in records:
            self.handler.emit(record)

        self.assertEqual(len(self.log_buffer), 3)
        self.assertEqual(list(self.log_buffer), ["INFO:Msg 2", "INFO:Msg 3", "INFO:Msg 4"])

if __name__ == "__main__":
    unittest.main()
