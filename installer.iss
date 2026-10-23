; Inno Setup script — PepeMonitor installer/uninstaller
#define MyAppName "PepeMonitor"
#define MyAppVersion "1.3"
#define MyAppExeName "PepeMonitor.exe"

[Setup]
AppId={{6F3D2A91-4C1B-4E57-9B8A-5E2C7D1F0A42}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Pepe5rog
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=PepeMonitorSetup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
SetupIconFile=frog.ico
UninstallDisplayIcon={app}\frog.ico
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Дополнительные значки:"
Name: "profileicons"; Description: "Ярлыки «Дневной» и «Ночной» на рабочем столе"; GroupDescription: "Дополнительные значки:"; Flags: unchecked

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "frog.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\frog.ico"
Name: "{group}\Дневной режим"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--profile ""Дневной"""; IconFilename: "{app}\frog.ico"
Name: "{group}\Ночной режим"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--profile ""Ночной"""; IconFilename: "{app}\frog.ico"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\frog.ico"; Tasks: desktopicon
Name: "{autodesktop}\{#MyAppName} — Дневной"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--profile ""Дневной"""; IconFilename: "{app}\frog.ico"; Tasks: profileicons
Name: "{autodesktop}\{#MyAppName} — Ночной"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--profile ""Ночной"""; IconFilename: "{app}\frog.ico"; Tasks: profileicons

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; added by the app's "Возвращать при входе в Windows" checkbox
Type: files; Name: "{userstartup}\{#MyAppName}.lnk"
