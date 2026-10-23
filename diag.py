# -*- coding: utf-8 -*-
"""
Diagnostic report for testing on other computers: what every screen reports and what actually works.
PepeMonitor.exe --diag [folder]  ->  <folder>/<COMPUTER>_<date>.json + .txt
The gamma test briefly tints the screens (about a second) and then restores the saved correction.
"""

import datetime
import json
import os
import platform
import subprocess
import sys
import winreg

import ddc
import displays

VCP_TO_READ = {
    0x10: "brightness", 0x12: "contrast", 0x14: "color preset", 0x16: "red gain", 0x18: "green gain",
    0x1A: "blue gain", 0x87: "sharpness", 0x60: "input source", 0x62: "volume", 0x72: "gamma",
    0xD6: "power mode", 0xDC: "display mode", 0xC6: "app enable key", 0xC8: "controller type",
    0xC9: "firmware level",
}
# temp K, gamma %, dim %
GAMMA_TESTS = [(5000, 100, 100), (4000, 100, 100), (3500, 100, 100), (2700, 100, 100),
               (6500, 100, 70), (6500, 100, 50), (6500, 100, 40), (6500, 100, 20),
               (6500, 70, 100), (6500, 150, 100)]


def _ps_json(script):
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return json.loads(r.stdout or "null")
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        return {"error": str(e)}


def _edid_hex(pnp_id, instance):
    path = r"SYSTEM\CurrentControlSet\Enum\DISPLAY\%s\%s\Device Parameters" % (pnp_id, instance)
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            return bytes(winreg.QueryValueEx(key, "EDID")[0]).hex()
    except OSError:
        return None


def system_info():
    return {
        "computer": platform.node(),
        "windows": platform.platform(),
        "model": _ps_json("Get-CimInstance Win32_ComputerSystem | Select Manufacturer,Model | ConvertTo-Json"),
        "gpus": _ps_json("Get-CimInstance Win32_VideoController | Select Name,DriverVersion,DriverDate,"
                         "CurrentHorizontalResolution,CurrentVerticalResolution | ConvertTo-Json"),
        "wmi_monitors": _ps_json("Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorConnectionParams "
                                 "-EA SilentlyContinue | Select InstanceName,VideoOutputTechnology | ConvertTo-Json"),
        "wide_gamma_range": displays.wide_gamma_range_enabled(),
    }


def display_info(d, settings, test_gamma):
    info = {
        "title": d.title, "name": d.name, "gdi_name": d.gdi_name, "adapter": d.adapter,
        "pnp_id": d.pnp_id, "instance": d.instance, "mode": d.mode, "kind": d.kind,
        "internal": d.internal, "virtual": d.virtual, "backlight": d.backlight, "clone": d.clone,
        "edid": _edid_hex(d.pnp_id, d.instance),
        "supports": [f for f in displays.FIELD_CODES if d.supports(f)],
    }
    if d.backlight:
        info["backlight_read"] = d.get("brightness")
    if d.ddc:
        info["ddc_caps_raw"] = d.ddc.caps_raw
        info["ddc_caps"] = {"%02X" % k: v for k, v in d.ddc.caps.items()}
        info["vcp"] = {"%02X %s" % (c, n): d.ddc.get(c) for c, n in VCP_TO_READ.items()}
    if test_gamma and d.soft_capable:
        results = {}
        for temp, gamma, dim in GAMMA_TESTS:
            results["%dK g%d d%d" % (temp, gamma, dim)] = d.set_soft(temp, gamma, dim)
        d.set_soft(6500, 100, 100)
        saved = settings["soft"].get(d.key)
        if saved:
            d.set_soft(saved["temp"], saved["gamma"], saved["dim"])
        info["gamma_ramp_accepted"] = results
    return info


def _text(report):
    lines = ["PepeMonitor %s — диагностика %s" % (report["app_version"], report["time"]), ""]
    s = report["system"]
    lines.append("Компьютер: %s  %s" % (s["computer"], s.get("model")))
    lines.append("Windows: %s" % s["windows"])
    gpus = s.get("gpus")
    for g in gpus if isinstance(gpus, list) else [gpus]:
        if isinstance(g, dict) and "Name" in g:
            lines.append("Видеокарта: %s, драйвер %s" % (g.get("Name"), g.get("DriverVersion")))
    lines.append("Снято ограничение gamma (GdiIcmGammaRange=256): %s" % s["wide_gamma_range"])
    for d in report["displays"]:
        lines += ["", "=== %s" % d["title"],
                  "  PnP %s, адаптер %s, тип %s" % (d["pnp_id"], d["adapter"], d["kind"]),
                  "  Управляется: %s" % (", ".join(d["supports"]) or "ничего аппаратно")]
        if "backlight_read" in d:
            lines.append("  Подсветка WMI: %s" % (d["backlight_read"],))
        if "ddc_caps_raw" in d:
            lines.append("  DDC caps: %s" % d["ddc_caps_raw"])
            for k, v in d["vcp"].items():
                if v is not None:
                    lines.append("    VCP %s = %s / %s" % (k, v[0], v[1]))
        if "gamma_ramp_accepted" in d:
            ok = [k for k, v in d["gamma_ramp_accepted"].items() if v]
            bad = [k for k, v in d["gamma_ramp_accepted"].items() if not v]
            lines.append("  Gamma ramp принято: %s" % ", ".join(ok))
            lines.append("  Gamma ramp отказ:   %s" % (", ".join(bad) or "—"))
    return "\n".join(lines) + "\n"


def run(folder, version, settings, test_gamma=True):
    found = displays.enumerate_displays()
    try:
        report = {
            "app_version": version,
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
            "frozen": getattr(sys, "frozen", False),
            "system": system_info(),
            "displays": [display_info(d, settings, test_gamma) for d in found],
        }
    finally:
        displays.release(found)
    os.makedirs(folder, exist_ok=True)
    base = os.path.join(folder, "%s_%s" % (platform.node(), datetime.datetime.now().strftime("%Y-%m-%d_%H%M")))
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(base + ".txt", "w", encoding="utf-8") as f:
        f.write(_text(report))
    return base
