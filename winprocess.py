"""Windows process identity, console modes, and task lifetime isolation."""
import ctypes
from ctypes import wintypes as wt
import os
import subprocess
import threading


if os.name == 'nt':
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k32.OpenProcess.restype = wt.HANDLE
    k32.CloseHandle.argtypes = [wt.HANDLE]
    k32.GetCurrentProcess.restype = wt.HANDLE
    k32.IsProcessInJob.argtypes = [wt.HANDLE, wt.HANDLE, ctypes.POINTER(wt.BOOL)]
    k32.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
    k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
    k32.CreateJobObjectW.restype = wt.HANDLE
    k32.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    k32.GetStdHandle.argtypes = [wt.DWORD]
    k32.GetStdHandle.restype = wt.HANDLE
    k32.GetConsoleMode.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    k32.SetConsoleMode.argtypes = [wt.HANDLE, wt.DWORD]


def stamp(pid):
    if os.name != 'nt':
        raise RuntimeError('WinScreen 需要 Windows。')
    h = k32.OpenProcess(0x1000, False, int(pid))
    if not h:
        return None
    try:
        code = wt.DWORD()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:
            return None
        ts = [wt.FILETIME() for _ in range(4)]
        if not k32.GetProcessTimes(h, *(ctypes.byref(t) for t in ts)):
            return None
        return str((ts[0].dwHighDateTime << 32) | ts[0].dwLowDateTime)
    finally:
        k32.CloseHandle(h)


def in_job():
    result = wt.BOOL()
    if not k32.IsProcessInJob(k32.GetCurrentProcess(), None, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    return bool(result.value)


def detached(args, logfile):
    # SSH hosts can put children into a kill-on-disconnect Job Object.
    # Never silently inherit that job when promising detached execution.
    # DETACHED_PROCESS is sufficient for console lifetime isolation. Adding
    # CREATE_NEW_PROCESS_GROUP also sets inherited Ctrl+C-ignore state.
    flags = subprocess.DETACHED_PROCESS
    if in_job():
        flags |= subprocess.CREATE_BREAKAWAY_FROM_JOB
    with open(logfile, 'ab', buffering=0) as output:
        try:
            process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=output,
                                       stderr=output, close_fds=True, creationflags=flags)
            # Reap the handle when possible; this daemon thread never owns task lifetime.
            threading.Thread(target=process.wait, daemon=True).start()
            return process
        except OSError as exc:
            if exc.winerror == 5 and in_job():
                raise RuntimeError('当前 SSH/启动器禁止后台进程脱离 Job Object。'
                                   '请从服务器 Windows 桌面双击 start-panel.cmd 后，在面板里创建会话。') from exc
            raise


class BasicLimits(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64),
                ('PerJobUserTimeLimit', ctypes.c_int64), ('LimitFlags', wt.DWORD),
                ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', wt.DWORD), ('Affinity', ctypes.c_size_t),
                ('PriorityClass', wt.DWORD), ('SchedulingClass', wt.DWORD)]


class IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
        'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', BasicLimits), ('IoInfo', IOCounters),
                ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]


def own_worker_job():
    """Worker owns the lifetime of its shell and descendants, not its client."""
    h = k32.CreateJobObjectW(None, None)
    if not h:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(h, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        k32.CloseHandle(h)
        raise ctypes.WinError(ctypes.get_last_error())
    if not k32.AssignProcessToJobObject(h, k32.GetCurrentProcess()):
        k32.CloseHandle(h)
        raise ctypes.WinError(ctypes.get_last_error())
    # Keep open until worker exit. Closing this job kills the worker too.
    return h


class ConsoleModes:
    def __init__(self):
        self.saved = []

    def __enter__(self):
        for std, output in [(-11, True), (-10, False)]:
            handle = k32.GetStdHandle(std & 0xffffffff)
            old = wt.DWORD()
            if k32.GetConsoleMode(handle, ctypes.byref(old)):
                self.saved.append((handle, old.value))
                mode = old.value | 4 if output else old.value & ~(1 | 2 | 4)
                k32.SetConsoleMode(handle, mode)
        return self

    def __exit__(self, *args):
        for handle, mode in self.saved:
            k32.SetConsoleMode(handle, mode)


def console_input_available():
    # Windows treats NUL as a character device: isatty() alone can be True
    # even when getwch() has no real console and would block forever.
    if os.name != 'nt':
        return False
    mode = wt.DWORD()
    handle = k32.GetStdHandle((-10) & 0xffffffff)
    return bool(k32.GetConsoleMode(handle, ctypes.byref(mode)))
