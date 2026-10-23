# PepeMonitor — заметки для Claude Code

Утилита для Windows 10/11 (Python + tkinter, собирается в один exe): настройки всех экранов компьютера в одном окне.
Автор — Pepe5rog (школьный админ). Отвечать по-русски, коротко.

## Что делает

| Экран | Механизм | Код |
|---|---|---|
| Внешний монитор с DDC/CI | `dxva2` → VCP: 10 яркость, 12 контраст, 87 резкость, 14 пресет, 16/18/1A R/G/B, 08 сброс цвета | `ddc.py` |
| Экран ноутбука | WMI `root/wmi` `WmiMonitorBrightness(Methods)` через powershell | `displays.py` |
| Любой физический экран | gamma ramp `SetDeviceGammaRamp`: теплота K, гамма %, затемнение % | `displays.py` |
| Виртуальный (IddCx: PepeDisplay/VDD, VideoOutputTechnology 16/17) | ничего: захват (DDA) идёт до gamma ramp | `displays.py` |
| Питание | все экраны — `SC_MONITORPOWER` (будит мышь); монитор с DDC/CI — VCP D6=04/01; таймер в окне; «гасить экран»/«сон» Windows — `powercfg` | `power.py` |

- `displays.py` — `enumerate_displays()`: EnumDisplayMonitors → GDI имя, режим (EnumDisplaySettings), адаптер,
  PnP-id/instance (EnumDisplayDevices + EDD_GET_DEVICE_INTERFACE_NAME), имя из EDID в реестре, WMI (тип подключения, подсветка),
  DDC (`ddc.open_monitor`). Класс `Display`: `supports/get/set(field)`, `set_soft(temp, gamma, dim)`.
- `ddc.py` — DDC/CI: строка возможностей (парсер `_parse_caps`), если её нет — пробует VCP по одному.
- `pepe_monitor.py` — окно, профили, CLI (`--profile "Имя"`, `--restore`, `--diag [папка] [--no-gamma]`). Все вызовы DDC/WMI идут
  через поток `Worker` (`latest()` — для ползунков отправляется только последнее значение).
- `power.py` — `screens_off()`, `get_timeout/set_timeout("display"|"sleep", минуты)` (powercfg, текущая схема, AC и DC).
- `diag.py` — отчёт о всех экранах в JSON + TXT (EDID, сырая строка DDC caps, чтение VCP, какие gamma ramp Windows принимает).
- `make_icon.py` → `frog.ico`/`frog.png`; `installer.iss` (Inno Setup 6); `build.cmd` — exe + установщик.

Настройки: `%APPDATA%\PepeMonitor\settings.json` — `profiles` (`{"displays": {PnP-id или "*": {"hw": {...}, "soft": {...}}}}`),
`last_preset` (последний пресет по PnP-id), `soft` (программная коррекция по PnP-id). Профили 1.0 (плоские) мигрируют в `load_settings`.

## Запуск и сборка на чистом компьютере

```
winget install -e --id Python.Python.3.12        # если нет python
python -m pip install pillow pyinstaller
python pepe_monitor.py                           # окно из исходников
python pepe_monitor.py --diag ..\Отчёты          # отчёт
winget install -e --id JRSoftware.InnoSetup      # только если нужен установщик
build.cmd                                        # dist\PepeMonitor.exe + installer_output\PepeMonitorSetup-<версия>.exe
```
`build.cmd` вызывать полным путём (`cmd /c "<полный путь>\build.cmd"`): из bash/PowerShell `cmd /c build.cmd` теряет текущую папку.
Inno ищется в `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe` (так ставит winget для пользователя); если стоит в Program Files — поправить путь в build.cmd.

## Известные особенности (проверено)

- **Realtek-скейлеры** (caps `model(RTK)`): пресет VCP 14 меняется, но при чтении монитор отдаёт не то значение →
  в окне подсвечивается последний пресет, установленный из программы (`last_preset`), а не прочитанный.
- **Яркость (VCP 10) принимается, но экран не меняется**: монитор сам блокирует яркость в режиме HDR/DCR/ECO
  (MHH2700: «автоматический HDR» в меню монитора; контраст VCP 12 при этом работает). Чтение VCP 10 возвращает записанное —
  программно это не отличить, только спросить пользователя и выключить режим в меню монитора.
- **Gamma ramp без `GdiIcmGammaRange=256`**: Windows отказывает при затемнении < 50% и теплоте 2700K (3500K проходит).
  Программа предлагает снять ограничение (UAC + перевход). `set_soft` возвращает False при отказе.
- **Виртуальные экраны**: `SetDeviceGammaRamp` возвращает True, но на устройстве ничего не меняется (проверено на PepeDisplay) —
  поэтому для них `soft_capable = False`.
- **Встроенный экран ноутбука** по DDC/CI не отвечает никогда, только WMI-подсветка.
- **Режим «Дублировать»**: на одном GDI-устройстве (одном HMONITOR) несколько экранов — перебирать все мониторы
  `EnumDisplayDevicesW(dev, j)`, не только №0 (иначе пропадал экран ноутбука при дублировании на проектор). Gamma ramp у таких
  экранов общий (`Display.clone`), WMI-подсветка и DDC — свои (проверено: Lenovo 81W8 + EPSON).
- **Питание по DDC/CI**: только D6=04 (DPM off — монитор слушает DDC/CI, будится «Вкл» или своей кнопкой), не 05 (жёсткое
  выключение). `supports("power")` — если в caps у D6 есть 04; только тогда в окне видна строка «Этот монитор: Выкл / Вкл».
  Проверено на HP 23bw (TEACH-PC2) и HP 22es (TEACH-PC1).
- **`Worker`**: callback `done` привязывать в lambda по умолчанию (`done=done`) — иначе при двух заданиях подряд ответ
  уходил чужому обработчику, тот падал, и `_drain` останавливался (окно «зависало»). `_drain` теперь перепланируется первым.
- **Масштаб 125%+**: окно DPI-aware; разрешение берётся из EnumDisplaySettings (rcMonitor даёт масштабированное).
- Опорный компьютер: ASUS TUF A15 FA506NF (AMD iGPU + RTX 2050), внешний 27" MHH2700 2560×1440 144 Гц на Realtek,
  виртуальный VDD MTT1337 (PepeDisplay). Отчёт по нему лежит в `..\Отчёты\` (если запущено с флешки).

## Правила изменений

- Можно менять любой код проекта, если на тестовом компьютере что-то не работает. Стиль: как в существующем коде
  (комментарии по-английски и только там, где неочевидно; тексты интерфейса по-русски; палитра `BG/CARD/GREEN/...` как в PepeDisplay).
- Не ломать совместимость `settings.json` (старые профили должны загружаться).
- Новый тип экрана/монитора, который ведёт себя странно — сначала собрать `--diag`, потом чинить, потом снова `--diag`.
- Опасные VCP **не трогать без явной просьбы**: 60 (вход — экран погаснет), D6 (питание), 04 (полный сброс монитора), DF/FD/FF.
- При правке версии менять и `VERSION` в `pepe_monitor.py`, и `MyAppVersion` в `installer.iss`.
- Ничего не пушить на GitHub и не коммитить без просьбы пользователя (репозиторий будет приватным Pepe5rog/PepeMonitor).
