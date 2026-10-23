# -*- coding: utf-8 -*-
"""DDC/CI access to external monitors through Windows dxva2 (no drivers needed)."""

import ctypes
import re
from ctypes import wintypes as W

_dxva2 = ctypes.windll.dxva2

BRIGHTNESS = 0x10
CONTRAST = 0x12
COLOR_PRESET = 0x14
RED_GAIN = 0x16
GREEN_GAIN = 0x18
BLUE_GAIN = 0x1A
SHARPNESS = 0x87
COLOR_RESET = 0x08
POWER_MODE = 0xD6
POWER_ON = 0x01
POWER_OFF = 0x04  # DPM off: the monitor still listens to DDC/CI; 0x05 (hard off) would need its power button

# MCCS 2.2 values of VCP 0x14
PRESET_NAMES = {
    0x01: "sRGB",
    0x02: "Родной",
    0x03: "4000K",
    0x04: "5000K",
    0x05: "6500K",
    0x06: "7500K",
    0x07: "8200K",
    0x08: "9300K",
    0x09: "10000K",
    0x0A: "11500K",
    0x0B: "Свой (RGB)",
    0x0C: "Свой 2",
    0x0D: "Свой 3",
}
USER_PRESET = 0x0B

# probed one by one when the monitor gives no capabilities string
_PROBE = [BRIGHTNESS, CONTRAST, COLOR_PRESET, RED_GAIN, GREEN_GAIN, BLUE_GAIN, SHARPNESS]


class _PhysicalMonitor(ctypes.Structure):
    _fields_ = [("handle", W.HANDLE), ("description", W.WCHAR * 128)]


def _parse_caps(caps):
    """'vcp(10 12 14(01 05 08) 16)' -> {0x10: [], 0x14: [1, 5, 8], ...}"""
    start = caps.lower().find("vcp(")
    if start < 0:
        return {}
    # cut the balanced vcp(...) group: other groups like mccs_ver(2.2) follow it
    depth, end = 0, len(caps)
    for j in range(start + 3, len(caps)):
        depth += {"(": 1, ")": -1}.get(caps[j], 0)
        if depth == 0:
            end = j
            break
    body, result, i = caps[start + 4:end], {}, 0
    tokens = re.findall(r"[0-9A-Fa-f]{2}|\(|\)", body)
    while i < len(tokens):
        if tokens[i] in "()":
            i += 1
            continue
        code = int(tokens[i], 16)
        values = []
        if i + 1 < len(tokens) and tokens[i + 1] == "(":
            i += 2
            depth = 1
            while i < len(tokens) and depth:
                if tokens[i] == "(":
                    depth += 1
                elif tokens[i] == ")":
                    depth -= 1
                elif depth == 1:
                    values.append(int(tokens[i], 16))
                i += 1
        else:
            i += 1
        result[code] = values
    return result


class Monitor:
    def __init__(self, handle):
        self.handle = handle
        self.caps = {}
        self.caps_raw = ""

    def supports(self, code):
        return code in self.caps

    def get(self, code):
        """(current, maximum) or None when the monitor does not answer."""
        cur, mx, kind = W.DWORD(), W.DWORD(), ctypes.c_int()
        if _dxva2.GetVCPFeatureAndVCPFeatureReply(self.handle, code, ctypes.byref(kind),
                                                  ctypes.byref(cur), ctypes.byref(mx)):
            return cur.value, mx.value
        return None

    def set(self, code, value):
        return bool(_dxva2.SetVCPFeature(self.handle, code, int(value)))

    def load_caps(self):
        """True if the monitor talks DDC/CI at all."""
        length = W.DWORD()
        if _dxva2.GetCapabilitiesStringLength(self.handle, ctypes.byref(length)):
            buf = ctypes.create_string_buffer(length.value)
            if _dxva2.CapabilitiesRequestAndCapabilitiesReply(self.handle, buf, length):
                self.caps_raw = buf.value.decode("latin1")
                self.caps = _parse_caps(self.caps_raw)
                if self.caps:
                    return True
        for code in _PROBE:
            if self.get(code):
                self.caps[code] = []
        return bool(self.caps)

    def release(self):
        _dxva2.DestroyPhysicalMonitor(self.handle)


def open_monitor(hmonitor, index=None):
    """The DDC/CI monitor behind a Windows HMONITOR, or None (laptop panels, TVs, virtual screens).
    index picks one physical monitor when several share the HMONITOR ("Duplicate" mode)."""
    count = W.DWORD()
    if not _dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmonitor, ctypes.byref(count)) or not count.value:
        return None
    arr = (_PhysicalMonitor * count.value)()
    if not _dxva2.GetPhysicalMonitorsFromHMONITOR(hmonitor, count, arr):
        return None
    found = None
    for i, pm in enumerate(arr):
        mon = Monitor(pm.handle)
        if found is None and (index is None or i == index) and mon.load_caps():
            found = mon
        else:
            mon.release()
    return found
