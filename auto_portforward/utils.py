import os
import signal
import sys
import ctypes


def set_pdeathsig(sig=signal.SIGTERM):
    """Set parent death signal on Linux so child dies if parent dies."""
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL("libc.so.6")
        return libc.prctl(1, sig)
    return 0


def preexec_set_pdeathsig():
    # Set the process group ID to the current process ID.
    os.setpgrp()
    # Set the session ID to the current process ID.
    # the following is done via the popen call
    # os.setsid()
    # Set the parent death signal to the current process ID.
    set_pdeathsig(signal.SIGTERM)
