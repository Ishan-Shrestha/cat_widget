; Inno Setup script — builds Buddy-Setup.exe from the PyInstaller output.
;
; Build locally (Windows, with Inno Setup installed):
;   iscc packaging\windows\installer.iss
;
; In CI this runs after `pyinstaller buddy.spec`, so dist\Buddy\ already
; exists with everything needed inside it. The {#Version} macro is passed
; in from the workflow (derived from the git tag) — falls back to 0.0.0
; for a local test build where you haven't set it.

#ifndef Version
  #define Version "0.0.0"
#endif

[Setup]
AppName=Buddy
AppVersion={#Version}
AppPublisher=Buddy
DefaultDirName={autopf}\Buddy
DefaultGroupName=Buddy
UninstallDisplayIcon={app}\Buddy.exe
OutputDir=..\..\dist_installer
OutputBaseFilename=Buddy-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
SetupIconFile=..\..\icons\icon.ico
DisableProgramGroupPage=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "..\..\dist\Buddy\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Buddy"; Filename: "{app}\Buddy.exe"
Name: "{autodesktop}\Buddy"; Filename: "{app}\Buddy.exe"; Tasks: desktopicon

[Run]
; Buddy manages its own "run at login" registration at runtime (Settings
; tab / startup.py) — the installer doesn't need its own separate
; autostart step, just an option to launch it once right after install.
Filename: "{app}\Buddy.exe"; Description: "Launch Buddy now"; Flags: nowait postinstall skipifsilent

; Note: Buddy's autostart registry entry (HKCU...\Run\Buddy) is created
; and removed by the app itself at runtime (Settings tab toggle), not by
; this installer — so it's not cleaned up automatically on uninstall.
; Minor known gap: turn the toggle off before uninstalling if you want
; that key gone too, otherwise it's a one-line manual regedit cleanup.
