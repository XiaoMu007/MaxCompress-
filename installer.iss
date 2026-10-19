#define AppName "MaxCompress"
#define AppVersion "2.6.18"
#define AppExe "MaxCompress.exe"

[Setup]
AppId={{0A2920DE-38E6-4C22-995D-17C9C4A3658A}
AppName={#AppName}
SetupIconFile=assets\app.ico
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableDirPage=no
UsePreviousAppDir=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer_output
OutputBaseFilename=MaxCompress_Setup_v{#AppVersion}
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
CloseApplications=yes
; 由安装器检测占用程序并询问用户，不能跳过正在占用的可执行文件

RestartApplications=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: checkedonce

[Files]
Source: "dist\MaxCompress\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\MaxCompress"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\MaxCompress"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "启动 MaxCompress"; Flags: nowait postinstall skipifsilent
