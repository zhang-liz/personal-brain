"""Tests use only local FalkorDB, even if your .env names a Cloud one. To test on Cloud, run: python tests/test_memory.py --cloud"""

import os

os.environ["FALKORDB_URL"] = ""
