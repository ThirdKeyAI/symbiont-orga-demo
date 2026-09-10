"""Trusted pre-execution gate for the Linux x86-64 observation lab.

Run with isolated Python startup (-I), from a read-only source mount. The
payload is executed only after the host acknowledges an attached observer.
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import platform
import socket
import sys


class Filter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]


class Program(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ushort), ("filters", ctypes.POINTER(Filter))]


def prepare_observation():
    if platform.machine() != "x86_64" or os.getuid() == 0:
        raise RuntimeError("observation gate requires non-root Linux x86-64")
    status = dict(line.split(":", 1) for line in open("/proc/self/status") if ":" in line)
    if any(int(status[name].strip(), 16) for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")):
        raise RuntimeError("observation worker must have no capabilities")
    libc = ctypes.CDLL(None, use_errno=True)

    def prctl(option, arg2, arg3=0):
        result = libc.prctl(ctypes.c_int(option), ctypes.c_ulong(arg2),
                            arg3, ctypes.c_ulong(0), ctypes.c_ulong(0))
        if result != 0:
            raise OSError(ctypes.get_errno(), "observation setup refused")

    prctl(4, 1)  # PR_SET_DUMPABLE: retain fd/path visibility for the observer
    prctl(0x59616D61, ctypes.c_ulong(-1).value)  # PR_SET_PTRACER_ANY
    # Fixed native ABI. Reject compat/x32 calls rather than applying native
    # syscall numbers to another ABI. Ordinary fork/clone/thread use remains.
    rules = [Filter(0x20, 0, 0, 4), Filter(0x15, 1, 0, 0xC000003E),
             Filter(0x06, 0, 0, 0x80000000), Filter(0x20, 0, 0, 0),
             Filter(0x45, 0, 1, 0x40000000), Filter(0x06, 0, 0, 0x80000000)]
    for number in (101, 425, 426, 427):  # ptrace and io_uring entry points
        rules += [Filter(0x15, 0, 1, number), Filter(0x06, 0, 0, 0x50000 | errno.EPERM)]
    # Keep the observation contract stable after the trusted gate completes.
    rules += [Filter(0x15, 0, 5, 157), Filter(0x20, 0, 0, 16),
              Filter(0x15, 0, 1, 4), Filter(0x06, 0, 0, 0x50000 | errno.EPERM),
              Filter(0x15, 0, 1, 0x59616D61), Filter(0x06, 0, 0, 0x50000 | errno.EPERM),
              Filter(0x20, 0, 0, 0)]
    rules += [Filter(0x15, 0, 1, 435), Filter(0x06, 0, 0, 0x50000 | errno.ENOSYS),
              Filter(0x15, 0, 3, 56), Filter(0x20, 0, 0, 16),
              Filter(0x45, 0, 1, 0x00800000),  # clone's CLONE_UNTRACED
              Filter(0x06, 0, 0, 0x50000 | errno.EPERM),
              Filter(0x06, 0, 0, 0x7FFF0000)]
    filters = (Filter * len(rules))(*rules)
    program = Program(len(rules), filters)
    prctl(38, 1)  # PR_SET_NO_NEW_PRIVS
    prctl(22, 2, ctypes.byref(program))  # PR_SET_SECCOMP, FILTER


def main():
    control, *command = sys.argv[1:]
    if not command or not command[0].startswith("/"):
        raise ValueError("gate requires an absolute executable")
    prepare_observation()
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(20)
        connection.connect(control)
        connection.sendall(json.dumps({"version": 1, "uid": os.getuid(), "pid": os.getpid()}).encode() + b"\n")
        response = bytearray()
        while len(response) < len(b"continue\n"):
            chunk = connection.recv(len(b"continue\n") - len(response))
            if not chunk:
                raise RuntimeError("observer closed the gate")
            response.extend(chunk)
        if response != b"continue\n":
            raise RuntimeError("observer did not authorize execution")
    os.execv(command[0], command)


if __name__ == "__main__":
    main()
