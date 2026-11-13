; Inno Setup script for SubPull (per-user install, no administrator rights needed).
; Build: iscc /DAppVersion=1.0.0 packaging\subpull.iss   (after the PyInstaller build)
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F3B2C1E-8B1D-4C55-9E2A-5B7E2C9D1A40}
AppName=SubPull
AppVersion={#AppVersion}
AppVerName=SubPull {#AppVersion}
AppPublisher=SubPull contributors
AppPublisherURL=https://github.com/kopersyn/subpull
AppSupportURL=https://github.com/kopersyn/subpull/issues
DefaultDirName={localappdata}\Programs\SubPull
DefaultGroupName=SubPull
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
LicenseFile=..\LICENSE
SetupIconFile=subpull.ico
UninstallDisplayIcon={app}\SubPull.exe
OutputDir=..\build\installer
OutputBaseFilename=SubPull-{#AppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\build\dist\SubPull\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\SubPull"; Filename: "{app}\SubPull.exe"
Name: "{autodesktop}\SubPull"; Filename: "{app}\SubPull.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\SubPull.exe"; Description: "{cm:LaunchProgram,SubPull}"; Flags: nowait postinstall skipifsilent

; Settings, history and logs in %LOCALAPPDATA%\SubPull are deliberately kept on uninstall.
