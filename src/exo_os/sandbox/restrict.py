"""Defense in depth inside the already isolated namespace; failures are fatal.

Landlock ABI 3 is the minimum (includes truncate and cross-directory references).
Seccomp denies networking, namespace changes and cross-process/kernel interfaces.
"""
from __future__ import annotations

import ctypes as c
import errno
import os
import platform
import resource

from .policy import SandboxError


class Ruleset(c.Structure):
    _fields_ = [("handled_access_fs", c.c_uint64)]


class Beneath(c.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", c.c_uint64), ("parent_fd", c.c_int32)]


def landlock(readonly: list[str], writable: list[str]) -> None:
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "aarch64"}:
        raise SandboxError("Landlock requires Linux x86_64 or aarch64")
    libc = c.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = c.c_long
    abi = syscall(444, 0, 0, 1)
    if abi < 3:
        raise SandboxError("Landlock ABI 3 or newer is required; tool refused")
    handled = (1 << 15) - 1  # all filesystem rights through TRUNCATE, ABI 3
    read = (1 << 0) | (1 << 2) | (1 << 3)  # execute, read file, read directory
    attr = Ruleset(handled)
    fd = syscall(444, c.byref(attr), c.sizeof(attr), 0)
    if fd < 0:
        raise SandboxError(f"cannot create Landlock policy: errno {c.get_errno()}")
    try:
        for path, rights in [(p, read) for p in readonly] + [(p, handled) for p in writable]:
            parent = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = Beneath(rights, parent)
                if syscall(445, fd, 1, c.byref(rule), 0) < 0:
                    raise SandboxError(f"cannot restrict filesystem: errno {c.get_errno()}")
            finally:
                os.close(parent)
        if libc.prctl(38, 1, 0, 0, 0) or syscall(446, fd, 0) < 0:
            raise SandboxError(f"cannot enforce Landlock: errno {c.get_errno()}")
    finally:
        os.close(fd)


def seccomp() -> None:
    try:
        lib = c.CDLL("libseccomp.so.2")
    except OSError as exc:
        raise SandboxError("libseccomp is required; tool refused") from exc
    lib.seccomp_init.argtypes = [c.c_uint32]
    lib.seccomp_init.restype = c.c_void_p
    lib.seccomp_rule_add.argtypes = [c.c_void_p, c.c_uint32, c.c_int, c.c_uint]
    lib.seccomp_syscall_resolve_name.argtypes = [c.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = c.c_int
    lib.seccomp_load.argtypes = [c.c_void_p]
    lib.seccomp_release.argtypes = [c.c_void_p]
    ctx = lib.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not ctx:
        raise SandboxError("cannot create syscall policy")
    denied = (
        "socket", "connect", "bind", "listen", "accept", "accept4", "sendmsg", "recvmsg",
        "mount", "umount2", "pivot_root", "unshare", "setns", "ptrace", "process_vm_readv",
        "process_vm_writev", "bpf", "perf_event_open", "keyctl", "add_key", "request_key",
        "userfaultfd", "io_uring_setup", "kexec_load", "init_module", "finit_module", "delete_module",
        "reboot", "open_by_handle_at",
    )
    try:
        for syscall in denied:
            number = lib.seccomp_syscall_resolve_name(syscall.encode())
            if number < 0 or lib.seccomp_rule_add(ctx, 0x00050000 | errno.EPERM, number, 0) != 0:
                raise SandboxError(f"cannot restrict syscall {syscall}")
        if lib.seccomp_load(ctx):
            raise SandboxError("cannot enforce syscall policy")
    finally:
        lib.seccomp_release(ctx)


def limits() -> None:
    for kind, amount in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_NOFILE, 128),
                         (resource.RLIMIT_FSIZE, 32 * 1024 * 1024), (resource.RLIMIT_CPU, 60),
                         (resource.RLIMIT_AS, 768 * 1024 * 1024), (resource.RLIMIT_NPROC, 256)):
        _, hard = resource.getrlimit(kind)
        amount = min(amount, hard) if hard != resource.RLIM_INFINITY else amount
        resource.setrlimit(kind, (amount, amount))
