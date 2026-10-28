; Subtitle Tool - Inno Setup 6 安装脚本
;
; 打包前先跑 PyInstaller：
;   python -m PyInstaller --noconfirm --clean subtitle-tool.spec
;
; 再编译本脚本：
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\subtitle-tool.iss
;
; 安装内容：
;   {app}                     程序本体（PyInstaller onedir 产物）
;   {app}\runtime             ffmpeg / llama-server / whisper-cli
;   {app}\models              whisper 语音识别模型 + silero VAD
; 不包含翻译模型（约 4.3GB），首次启动时由程序下载到
;   %LOCALAPPDATA%\SubtitleTool\models

#define AppName        "Subtitle Tool"
#define AppVersion     "1.0.0"
#define AppPublisher   "REN"
#define AppExeName     "SubtitleTool.exe"
#define AppId          "{{7C4E2B18-9D3A-4F62-8E15-2B7A6C0D9F31}"

; 下面三个路径按需覆盖，例如：
;   ISCC.exe /DDistDir=D:\build\SubtitleTool /DRuntimeSrc=D:\runtime subtitle-tool.iss
#ifndef DistDir
  #define DistDir    "..\dist\SubtitleTool"
#endif
#ifndef RuntimeSrc
  #define RuntimeSrc "..\runtime"
#endif
#ifndef ModelSrc
  #define ModelSrc   "..\models"
#endif


[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\SubtitleTool
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes

; 装到用户目录，全程不需要管理员权限
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

OutputDir=output
OutputBaseFilename=SubtitleTool-{#AppVersion}-Setup
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}

Compression=lzma2/max
SolidCompression=no
WizardStyle=modern
ShowLanguageDialog=auto


[Languages]
Name: "cn"; MessagesFile: "ChineseSimplified.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"


[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked


[Files]
; 程序本体
Source: "{#DistDir}\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#DistDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

; 运行库（ffmpeg / llama / whisper，自带 DLL）
Source: "{#RuntimeSrc}\*"; DestDir: "{app}\runtime"; Flags: ignoreversion recursesubdirs createallsubdirs

; 字幕模型：whisper 大模型 1.6GB（压完约 1.34GB）+ silero VAD。
; 装完即可用，不需要另外下载。
Source: "{#ModelSrc}\ggml-large-v3-turbo.bin"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelSrc}\ggml-silero-v6.2.0.bin"; DestDir: "{app}\models"; Flags: ignoreversion

; 许可证与第三方组件说明（GPL 要求随二进制一起提供）
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}\licenses"; Flags: ignoreversion
Source: "..\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion


[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon


[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent


[Code]
{ 卸载时问一句要不要连翻译模型一起删掉——那是用户自己下载的，删了要重下 4.6GB }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;

  DataDir := ExpandConstant('{localappdata}\SubtitleTool');
  if not DirExists(DataDir) then
    Exit;

  if MsgBox('是否同时删除已下载的翻译模型和配置文件？' + #13#10 +
            '如果只重装程序，建议保留。' + #13#10 + #13#10 + DataDir,
            mbConfirmation, MB_YESNO) = IDYES then
    DelTree(DataDir, True, True, True);
end;
