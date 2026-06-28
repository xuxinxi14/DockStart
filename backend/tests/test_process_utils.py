import subprocess
import sys
import unittest

from dockstart_core.process_utils import hidden_subprocess_kwargs


class ProcessUtilsTests(unittest.TestCase):
    def test_hidden_subprocess_kwargs_are_windows_only(self) -> None:
        kwargs = hidden_subprocess_kwargs()
        if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            self.assertEqual(kwargs, {"creationflags": subprocess.CREATE_NO_WINDOW})
        else:
            self.assertEqual(kwargs, {})


if __name__ == "__main__":
    unittest.main()
