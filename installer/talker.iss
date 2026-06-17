; Inno Setup — инсталлер Talker (один TalkerSetup.exe).
; Собрать:  ISCC.exe installer\talker.iss     (после `python build.py`)
; Источник = dist\Talker\ (lite-сборка без модели; модель скачается при 1-м запуске).

#define MyAppName "Talker"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Cut & Ship"
#define MyAppURL "https://cutandship.dev"
#define MyAppExeName "Talker.exe"

[Setup]
; ВНИМАНИЕ: не менять AppId между версиями — по нему находится прошлая установка.
AppId={{B8E7B6A2-4C3D-4E5F-9A1B-7C6D5E4F3A2B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
; Per-user в LocalAppData — без прав администратора и папка доступна на запись
; (Talker пишет config.toml/talker.log и качает модель рядом с собой).
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=.
OutputBaseFilename=TalkerSetup
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startup"; Description: "Запускать Talker при входе в Windows"; GroupDescription: "Автозапуск:"

[Files]
Source: "..\dist\Talker\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startup

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
