"""Verbindung zu lokalen IPC-Endpunkten (mpv, Discord).

Linux/macOS: Unix-Socket. Windows: Named Pipe (\\\\.\\pipe\\...) mit denselben
Methoden wie ein Socket (sendall, recv, close), damit der restliche Code nicht
zwischen den Systemen unterscheiden muss.
"""

from __future__ import annotations

import socket
import sys
import time

WINDOWS = sys.platform == "win32"


def connect(path: str, timeout: float):
    """Verbindet sich mit path. recv() der zurückgegebenen Verbindung wirft
    socket.timeout, wenn `timeout` Sekunden lang nichts ankommt."""
    if WINDOWS:
        return _WindowsPipe(path, timeout)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(path)
    except OSError:
        sock.close()
        raise
    return sock


class _WindowsPipe:
    """Named Pipe, gelesen per PeekNamedPipe mit Zeitlimit: ein blockierendes
    ReadFile würde auf einer synchronen Pipe auch jedes gleichzeitige
    WriteFile aufhalten - und ohne Zeitlimit hinge die TUI, falls mpv hängt."""

    def __init__(self, path: str, timeout: float):
        import ctypes
        import msvcrt
        from ctypes import wintypes

        self._timeout = timeout
        # FileNotFoundError, solange es die Pipe (noch) nicht gibt
        self._file = open(path, "r+b", buffering=0)
        self._handle = msvcrt.get_osfhandle(self._file.fileno())
        self._peek = ctypes.windll.kernel32.PeekNamedPipe
        self._peek.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        self._peek.restype = wintypes.BOOL
        self._available = wintypes.DWORD()
        self._available_ref = ctypes.byref(self._available)

    def sendall(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            view = view[self._file.write(view) :]

    def recv(self, n: int) -> bytes:
        deadline = time.monotonic() + self._timeout
        while True:
            if not self._peek(self._handle, None, 0, None, self._available_ref, None):
                return b""  # Gegenseite hat die Pipe geschlossen (wie bei Sockets)
            if self._available.value:
                return self._file.read(min(n, self._available.value))
            if time.monotonic() >= deadline:
                raise socket.timeout("timed out")
            time.sleep(0.001)

    def close(self) -> None:
        self._file.close()
