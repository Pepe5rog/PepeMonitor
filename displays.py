# -*- coding: utf-8 -*-
"""
Every active screen Windows knows about and the ways to control it:
  * DDC/CI      — external monitors: real brightness/contrast/colour inside the monitor;
  * backlight   — laptop panels: brightness through WMI (the same as the Fn keys);
  * gamma ramp  — any physical screen: warmth, gamma and dimming applied by the video card.
Virtual screens (PepeDisplay, other IddCx drivers) are captured before the gamma ramp,
so for them the ramp does nothing.
"""

import ctypes
import json
import math
import subprocess
import winreg
from ctypes import wintypes as W

import ddc

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32

# D3DKMDT_VIDEO_OUTPUT_TECHNOLOGY
VOT_INTERNAL = 0x80000000
VOT_INDIRECT = (16, 17)

NEUTRAL_SOFT = {"temp": 6500, "gamma": 100, "dim": 100}


class _MonitorInfoEx(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT),
                ("dwFlags", W.DWORD), ("szDevice", W.WCHAR * 32)]


class _DisplayDevice(ctypes.Structure):
    _fields_ = [("cb", W.DWORD), ("DeviceName", W.WCHAR * 32), ("DeviceString", W.WCHAR * 128),
                ("StateFlags", W.DWORD), ("DeviceID", W.WCHAR * 128), ("DeviceKey", W.WCHAR * 128)]


class _DevMode(ctypes.Structure):
    _fields_ = [("dmDeviceName", W.WCHAR * 32), ("dmSpecVersion", W.WORD), ("dmDriverVersion", W.WORD),
                ("dmSize", W.WORD), ("dmDriverExtra", W.WORD), ("dmFields", W.DWORD),
                ("dmPosition", W.LONG * 2), ("dmDisplayOrientation", W.DWORD), ("dmDisplayFixedOutput", W.DWORD),
                ("dmColor", W.SHORT), ("dmDuplex", W.SHORT), ("dmYResolution", W.SHORT),
                ("dmTTOption", W.SHORT), ("dmCollate", W.SHORT), ("dmFormName", W.WCHAR * 32),
                ("dmLogPixels", W.WORD), ("dmBitsPerPel", W.DWORD), ("dmPelsWidth", W.DWORD),
                ("dmPelsHeight", W.DWORD), ("dmDisplayFlags", W.DWORD), ("dmDisplayFrequency", W.DWORD),
                ("rest", W.DWORD * 8)]


_ENUM_PROC = ctypes.WINFUNCTYPE(W.BOOL, W.HMONITOR, W.HDC, ctypes.POINTER(W.RECT), W.LPARAM)


def _powershell(script, timeout=15):
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode, r.stdout.strip()


def _wmi_info():
    """{'DISPLAY\\CMN1521\\5&..._0': {'tech': int, 'brightness': int|None}} from root/wmi."""
    script = (
        "$o=@{};"
        "Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorConnectionParams -EA SilentlyContinue |"
        " % { $o[$_.InstanceName]=@{tech=[int64]$_.VideoOutputTechnology;brightness=$null} };"
        "Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightness -EA SilentlyContinue |"
        " % { if(-not $o[$_.InstanceName]){$o[$_.InstanceName]=@{tech=$null}};"
        " $o[$_.InstanceName].brightness=[int]$_.CurrentBrightness };"
        "$o | ConvertTo-Json -Compress")
    try:
        code, out = _powershell(script)
        return {k.upper(): v for k, v in json.loads(out or "{}").items()} if code == 0 else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def _edid_name(pnp_id, instance):
    """Model name from the EDID stored in the registry (descriptor 0xFC), or None."""
    path = r"SYSTEM\CurrentControlSet\Enum\DISPLAY\%s\%s\Device Parameters" % (pnp_id, instance)
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            edid = bytes(winreg.QueryValueEx(key, "EDID")[0])
    except OSError:
        return None
    for i in range(54, 126, 18):
        d = edid[i:i + 18]
        if d[0] == 0 and d[1] == 0 and d[3] == 0xFC:
            return d[5:].split(b"\n")[0].decode("latin1").strip() or None
    return None


def kelvin_to_rgb(kelvin):
    """Channel multipliers for a white point, 6500K = (1, 1, 1). Tanner Helland's fit of the black body."""
    def raw(k):
        t = k / 100
        r = 255 if t <= 66 else 329.698727446 * (t - 60) ** -0.1332047592
        g = 99.4708025861 * math.log(t) - 161.1195681661 if t <= 66 else 288.1221695283 * (t - 60) ** -0.0755148492
        b = 255 if t >= 66 else (0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307)
        return [min(255.0, max(0.0, c)) for c in (r, g, b)]
    ref = raw(6500)
    return tuple(min(1.0, c / n) for c, n in zip(raw(kelvin), ref))


def wide_gamma_range_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ICM") as k:
            return winreg.QueryValueEx(k, "GdiIcmGammaRange")[0] == 256
    except OSError:
        return False


def enable_wide_gamma_range():
    """Windows rejects strong gamma ramps (deep dimming, warm night colours) unless this is set. Asks for admin."""
    reg = r"reg add 'HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ICM' /v GdiIcmGammaRange /t REG_DWORD /d 256 /f"
    code, _ = _powershell("try { Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
                          "-ArgumentList '-NoProfile','-Command',\"%s\"; exit 0 } catch { exit 1 }"
                          % reg.replace("'", "''"), timeout=120)
    return code == 0 and wide_gamma_range_enabled()


class Display:
    def __init__(self, gdi_name, mode, adapter, pnp_id, instance, name):
        self.gdi_name = gdi_name
        self.mode = mode  # (width, height, hz) in real pixels, independent of DPI scaling
        self.adapter = adapter
        self.pnp_id = pnp_id
        self.instance = instance
        self.name = name
        self.ddc = None
        self.backlight = False
        self.internal = False
        self.virtual = False
        self.clone = False  # "Duplicate" mode: shares the GDI device, so also the gamma ramp, with another screen

    @property
    def key(self):
        """Stable id for saving settings: model code from EDID."""
        return self.pnp_id or self.gdi_name

    @property
    def kind(self):
        if self.virtual:
            return "виртуальный"
        if self.internal:
            return "экран ноутбука"
        return "DDC/CI" if self.ddc else "без DDC/CI"

    @property
    def title(self):
        w, h, hz = self.mode
        return "%s · %d×%d %d Гц · %s%s" % (self.name, w, h, hz, self.kind, " · дублирование" if self.clone else "")

    @property
    def soft_capable(self):
        return not self.virtual

    def supports(self, field):
        if field == "brightness":
            return self.backlight or (self.ddc is not None and self.ddc.supports(ddc.BRIGHTNESS))
        if field == "power":  # only the soft "DPM off" the monitor can wake from by DDC/CI
            values = self.ddc.caps.get(ddc.POWER_MODE) if self.ddc else None
            return values is not None and (not values or ddc.POWER_OFF in values)
        code = FIELD_CODES.get(field)
        return code is not None and self.ddc is not None and self.ddc.supports(code)

    def get(self, field):
        """(current, maximum) or None."""
        if field == "brightness" and self.backlight and not (self.ddc and self.ddc.supports(ddc.BRIGHTNESS)):
            code, out = _powershell("(Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightness |"
                                    " ? InstanceName -like '%s*').CurrentBrightness" % self._wmi_prefix())
            return (int(out), 100) if code == 0 and out.isdigit() else None
        if not self.supports(field):
            return None
        return self.ddc.get(FIELD_CODES[field])

    def set(self, field, value):
        if field == "brightness" and self.backlight and not (self.ddc and self.ddc.supports(ddc.BRIGHTNESS)):
            code, _ = _powershell("Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightnessMethods |"
                                  " ? InstanceName -like '%s*' | Invoke-CimMethod -MethodName WmiSetBrightness"
                                  " -Arguments @{Timeout=[uint32]0;Brightness=[byte]%d} | Out-Null"
                                  % (self._wmi_prefix(), int(value)))
            return code == 0
        if not self.supports(field):
            return False
        return self.ddc.set(FIELD_CODES[field], value)

    def set_soft(self, temp, gamma, dim):
        """Gamma ramp: temp in K (6500 = neutral), gamma and dim in percent (100 = neutral)."""
        mult = kelvin_to_rgb(temp)
        g = max(0.3, gamma / 100)
        d = max(0.0, min(1.0, dim / 100))
        ramp = (W.WORD * 768)()
        for i in range(256):
            level = (i / 255) ** (1 / g) * d
            for c in range(3):
                ramp[c * 256 + i] = min(65535, int(round(level * mult[c] * 65535)))
        hdc = _gdi32.CreateDCW("DISPLAY", self.gdi_name, None, None)
        if not hdc:
            return False
        try:
            return bool(_gdi32.SetDeviceGammaRamp(hdc, ramp))
        finally:
            _gdi32.DeleteDC(hdc)

    def _wmi_prefix(self):
        return ("DISPLAY\\%s\\%s" % (self.pnp_id, self.instance)).replace("'", "''")

    def release(self):
        if self.ddc:
            self.ddc.release()
            self.ddc = None


FIELD_CODES = {
    "brightness": ddc.BRIGHTNESS,
    "contrast": ddc.CONTRAST,
    "sharpness": ddc.SHARPNESS,
    "preset": ddc.COLOR_PRESET,
    "red": ddc.RED_GAIN,
    "green": ddc.GREEN_GAIN,
    "blue": ddc.BLUE_GAIN,
    "power": ddc.POWER_MODE,
}


def enumerate_displays():
    hmons = []
    proc = _ENUM_PROC(lambda hm, hdc, rc, lp: hmons.append(hm) or True)
    _user32.EnumDisplayMonitors(None, None, proc, 0)
    wmi = _wmi_info()

    result = []
    for hm in hmons:
        info = _MonitorInfoEx()
        info.cbSize = ctypes.sizeof(info)
        _user32.GetMonitorInfoW(hm, ctypes.byref(info))

        dm = _DevMode()
        dm.dmSize = ctypes.sizeof(dm)
        _user32.EnumDisplaySettingsW(info.szDevice, -1, ctypes.byref(dm))  # ENUM_CURRENT_SETTINGS
        mode = (dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency)

        adapter = ""
        for i in range(32):
            a = _DisplayDevice()
            a.cb = ctypes.sizeof(a)
            if not _user32.EnumDisplayDevicesW(None, i, ctypes.byref(a), 0):
                break
            if a.DeviceName == info.szDevice:
                adapter = a.DeviceString
                break

        # In "Duplicate" mode several screens hang off one GDI device: list each of them
        monitors = []
        for j in range(8):
            dev = _DisplayDevice()
            dev.cb = ctypes.sizeof(dev)
            if not _user32.EnumDisplayDevicesW(info.szDevice, j, ctypes.byref(dev), 1):  # EDD_GET_DEVICE_INTERFACE_NAME
                break
            if not dev.StateFlags & 1:  # DISPLAY_DEVICE_ACTIVE
                continue
            parts = dev.DeviceID.split("#")
            monitors.append((parts[1], parts[2]) if len(parts) >= 3 else ("", ""))
        if not monitors:
            monitors = [("", "")]

        for index, (pnp_id, instance) in enumerate(monitors):
            name = (_edid_name(pnp_id, instance) if pnp_id else None) or ""
            d = Display(info.szDevice, mode, adapter, pnp_id, instance, name)
            d.clone = len(monitors) > 1
            w = next((v for k, v in wmi.items() if k.startswith(("DISPLAY\\%s\\%s" % (pnp_id, instance)).upper())), {})
            tech = w.get("tech")
            d.internal = tech is not None and tech & 0xFFFFFFFF == VOT_INTERNAL
            d.virtual = tech in VOT_INDIRECT or "virtual" in adapter.lower() or "idd" in adapter.lower()
            d.backlight = w.get("brightness") is not None
            if not d.internal and not d.virtual:
                d.ddc = ddc.open_monitor(hm, index if d.clone else None)
            if not d.name or d.name.lower() in ("monitor", "generic pnp monitor"):
                d.name = "Экран ноутбука" if d.internal else ("%s (%s)" % (d.name or "Монитор", pnp_id) if pnp_id else "Монитор")
            result.append(d)
    return result


def release(displays):
    for d in displays:
        d.release()
