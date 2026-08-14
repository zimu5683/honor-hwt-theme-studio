#ifndef AppVersion
  #error AppVersion must be provided with /DAppVersion=x.y.z
#endif

#ifndef SourceDir
  #define SourceDir "..\dist\大雪主题编辑器"
#endif

#ifndef OutputDir
  #define OutputDir ".."
#endif

#ifndef OutputBaseFilename
  #define OutputBaseFilename "HwtThemeStudio-Setup"
#endif

#ifndef LanguageFile
  #define LanguageFile "compiler:Default.isl"
#endif

[Setup]
AppId={{F26CC680-8350-4CA1-B39A-4A42716339EF}
AppName=大雪主题编辑器
AppVersion={#AppVersion}
AppPublisher=zimu5683
AppPublisherURL=https://github.com/zimu5683/honor-hwt-theme-studio
AppSupportURL=https://github.com/zimu5683/honor-hwt-theme-studio/issues
AppUpdatesURL=https://github.com/zimu5683/honor-hwt-theme-studio/releases
DefaultDirName={localappdata}\Programs\HwtThemeStudio
DefaultGroupName=大雪主题编辑器
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
UsePreviousAppDir=yes
UninstallDisplayIcon={app}\大雪主题编辑器.exe
LicenseFile=..\LICENSE

[Languages]
Name: "chinesesimplified"; MessagesFile: "{#LanguageFile}"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: unchecked

[InstallDelete]
Type: files; Name: "{app}\大雪主题编辑器.exe"
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\大雪主题编辑器"; Filename: "{app}\大雪主题编辑器.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\大雪主题编辑器"; Filename: "{app}\大雪主题编辑器.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\大雪主题编辑器.exe"; Description: "启动大雪主题编辑器"; Flags: nowait postinstall skipifsilent
