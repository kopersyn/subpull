"""Run the whole test suite and exit with its result, skipping interpreter teardown.

PySide6 on Linux with Python 3.10 can abort in interpreter shutdown *after* every
test has passed (a known issue with Qt object destruction order at exit). The test
outcome is what matters, so CI uses ``os._exit`` with the suite's own result.
Run locally with plain ``python -m unittest discover -s tests -t .``.
"""
import os
import sys
import unittest

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover("tests", top_level_dir=".")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if result.wasSuccessful() else 1)
