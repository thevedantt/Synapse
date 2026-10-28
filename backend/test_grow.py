import os
import unittest
from unittest.mock import patch

from grow import get_database_url


class TestGetDatabaseURL(unittest.TestCase):
    def test_strips_trailing_whitespace(self) -> None:
        with patch.dict(os.environ, {"DATABASE_URL": "postgres://db?channel_binding=require\n "}):
            self.assertEqual(get_database_url(), "postgres://db?channel_binding=require")


if __name__ == "__main__":
    unittest.main()
