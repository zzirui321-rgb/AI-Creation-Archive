"""Preload the explicitly selected SQLite DLL in local Python child processes."""

import ctypes
import os

if os.name == "nt" and os.environ.get("AICA_TEST_SQLITE_DLL"):
    _sqlite_dll = ctypes.WinDLL(os.environ["AICA_TEST_SQLITE_DLL"])
