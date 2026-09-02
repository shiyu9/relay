"""How the SessionEnd hook starts a recorder that outlives it.

Measured, 2026-09-02, across 42 sessions on Windows (CLI 2.1.252):

  A hook's process tree is killed when the hook exceeds its timeout — and
  only then. Inside the timeout, a child started with DETACHED_PROCESS
  survives its parent perfectly well (30s, 3/3 with a hook that exits
  immediately). Over the timeout, DETACHED and CREATE_BREAKAWAY_FROM_JOB
  both die (1-2s, 15/15). So the killer walks the tree; it is not the Job
  Object, which BREAKAWAY would have escaped.

  A timeout declared in a plugin's hooks.json is ignored — 5 and 30 behave
  exactly like no declaration at all (1.0-1.5s, 6/6). The same declaration
  in settings.json works (10s completion, 5/5). relay ships as a plugin, so
  **relay cannot buy itself more time**; 1.5 seconds is a ceiling it has to
  respect on its own.

Two things follow. The hook body must finish in well under a second — it
starts one process and writes one line, measured at 20ms. And the recorder
is given a parent outside the hook's tree anyway, because the ceiling can
be blown by something relay does not control (an antivirus scanning
python.exe on launch costs hundreds of milliseconds, sometimes seconds).
Re-parenting costs 8ms over a plain spawn, which is not worth a branch:
it is simply how the recorder is started, with DETACHED as the fallback
for the case where no adoptive parent can be opened.
"""
import ctypes
import os
import subprocess
import sys
from ctypes import wintypes

# Processes that can adopt the recorder: same user, alive as long as the
# desktop session is. Tried in order; the first one that opens wins.
ADOPTERS = ("explorer.exe", "sihost.exe", "RuntimeBroker.exe")

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_CREATE_PROCESS = 0x0080
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
PROC_THREAD_ATTRIBUTE_PARENT_PROCESS = 0x00020000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
QUIET = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
         "stderr": subprocess.DEVNULL, "close_fds": True}


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD),
                ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD),
                ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD),
                ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD),
                ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
                ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE)]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW),
                ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE),
                ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD),
                ("dwThreadId", wintypes.DWORD)]


def _k32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def find_pid(exe_name):
    """One process id for `exe_name`, or None."""
    k32 = _k32()
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        return None
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not k32.Process32FirstW(snap, ctypes.byref(entry)):
            return None
        while True:
            if entry.szExeFile.lower() == exe_name.lower():
                return entry.th32ProcessID
            if not k32.Process32NextW(snap, ctypes.byref(entry)):
                return None
    finally:
        k32.CloseHandle(snap)


def _spawn_under(command_line, ppid):
    """Start `command_line` as a child of `ppid`. (pid, None) or (None, why)."""
    k32 = _k32()
    handle = k32.OpenProcess(PROCESS_CREATE_PROCESS, False, ppid)
    if not handle:
        return None, f"OpenProcess({ppid}) failed {ctypes.get_last_error()}"
    try:
        size = ctypes.c_size_t(0)
        k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        buf = (ctypes.c_byte * size.value)()
        attrs = ctypes.cast(buf, ctypes.c_void_p)
        if not k32.InitializeProcThreadAttributeList(attrs, 1, 0,
                                                     ctypes.byref(size)):
            return None, f"InitializeProcThreadAttributeList failed " \
                         f"{ctypes.get_last_error()}"
        parent = wintypes.HANDLE(handle)
        if not k32.UpdateProcThreadAttribute(
                attrs, 0, ctypes.c_size_t(PROC_THREAD_ATTRIBUTE_PARENT_PROCESS),
                ctypes.byref(parent), ctypes.sizeof(parent), None, None):
            return None, f"UpdateProcThreadAttribute failed " \
                         f"{ctypes.get_last_error()}"

        si = STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        si.lpAttributeList = attrs
        pi = PROCESS_INFORMATION()
        ok = k32.CreateProcessW(
            None, ctypes.create_unicode_buffer(command_line), None, None,
            False, (EXTENDED_STARTUPINFO_PRESENT | DETACHED_PROCESS
                    | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW),
            None, None, ctypes.byref(si), ctypes.byref(pi))
        if not ok:
            return None, f"CreateProcessW failed {ctypes.get_last_error()}"
        k32.CloseHandle(pi.hProcess)
        k32.CloseHandle(pi.hThread)
        k32.DeleteProcThreadAttributeList(attrs)
        return pi.dwProcessId, None
    finally:
        k32.CloseHandle(handle)


def detached(argv):
    """Plain detached start. (pid, None) or (None, why)."""
    kw = dict(QUIET)
    if os.name == "nt":
        kw["creationflags"] = (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                               | CREATE_NO_WINDOW)
    else:
        # POSIX: setsid makes init the parent once this process exits, which
        # is the same escape by a different route. Unverified — relay is only
        # tested on Windows.
        kw["start_new_session"] = True
    try:
        return subprocess.Popen(argv, **kw).pid, None
    except OSError as e:
        return None, f"Popen failed {e!r}"


def start(argv):
    """Start the recorder outside this hook's process tree.

    Returns (pid, how) where `how` names the route taken, so the log can say
    which one ran: a run that fell back to "detached" is one where the 1.5s
    ceiling is the only thing protecting the recorder.
    """
    if os.name != "nt":
        pid, why = detached(argv)
        return pid, ("detached" if pid else f"failed: {why}")

    line = subprocess.list2cmdline(argv)
    for name in ADOPTERS:
        ppid = find_pid(name)
        if ppid is None:
            continue
        pid, why = _spawn_under(line, ppid)
        if pid:
            return pid, f"reparented to {name}({ppid})"
    pid, why = detached(argv)
    return pid, ("detached (no adoptive parent)" if pid else f"failed: {why}")
