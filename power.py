# -*- coding: utf-8 -*-
"""Turning screens off and the Windows power-plan timeouts ("turn off the display after", "sleep after")."""

import ctypes
import re
import subprocess

_user32 = ctypes.windll.user32

# powercfg aliases: (subgroup, setting, /change name without -ac/-dc)
TIMEOUTS = {
    "display": ("SUB_VIDEO", "VIDEOIDLE", "monitor-timeout"),
    "sleep": ("SUB_SLEEP", "STANDBYIDLE", "standby-timeout"),
}


def screens_off():
    """All screens go to standby, the same as the power plan timeout: any mouse move or key wakes them."""
    # PostMessage, not SendMessage: a hung window would block a broadcast SendMessage forever
    return bool(_user32.PostMessageW(0xFFFF, 0x0112, 0xF170, 2))  # HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER


def _powercfg(*args):
    r = subprocess.run(["powercfg"] + list(args), capture_output=True, text=True, errors="replace", timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode, r.stdout


def get_timeout(kind):
    """Minutes on mains power (0 = never) or None."""
    sub, setting, _ = TIMEOUTS[kind]
    try:
        code, out = _powercfg("/q", "SCHEME_CURRENT", sub, setting)
    except (OSError, subprocess.TimeoutExpired):
        return None
    # the text is localized; the last two hex numbers are always the AC and DC values in seconds
    values = re.findall(r"0x([0-9a-fA-F]{8})", out)
    return int(values[-2], 16) // 60 if code == 0 and len(values) >= 2 else None


def set_timeout(kind, minutes):
    """Sets both mains and battery values of the current plan; no admin rights needed."""
    name = TIMEOUTS[kind][2]
    try:
        return all(_powercfg("/change", "%s-%s" % (name, side), str(int(minutes)))[0] == 0 for side in ("ac", "dc"))
    except (OSError, subprocess.TimeoutExpired):
        return False
