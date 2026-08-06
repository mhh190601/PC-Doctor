; 电脑医生 安装脚本
#define MyAppName "电脑医生"
#define MyAppVersion "1.6.0"
#define MyAppPublisher "mhh190601"
#define MyAppURL "https://github.com/mhh190601/PC-Doctor"
#define MyAppExeName "main.exe"

[Setup]
; 基本设置
AppId={{B5A7F4E1-9C2D-4A3F-8E6B-1D5A0C7F8E9B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=.\Output
OutputBaseFilename=电脑医生安装包_v{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin

; 安装程序图标（如果有 pc_doctor.ico 则使用，否则可以注释掉下面这行）
SetupIconFile=pc_doctor.ico
; 卸载程序图标（使用主程序的图标，如果 main.exe 已嵌入图标则可直接用 exe，否则可以指向 ico 文件）
UninstallDisplayIcon={app}\{#MyAppExeName}

; 本机 Inno Setup 未安装中文语言包，使用内置默认英文界面（程序本身为中文，不影响使用）
; 如需中文安装界面，将 ChineseSimplified.isl 放入 Inno Setup 的 Languages 目录后取消下行注释
; Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; GroupDescription: "附加图标:"

[Files]
; 主程序（PyInstaller 打包，已内含 web 前端、模型、图标等资源）
Source: "main.exe"; DestDir: "{app}"; Flags: ignoreversion
; 知识库文件（首次安装时创建，后续不覆盖，避免丢失用户补充的知识）
Source: "knowledge_base.json"; DestDir: "{app}"; Flags: onlyifdoesntexist
Source: "knowledge_base_v2.json"; DestDir: "{app}"; Flags: onlyifdoesntexist
Source: "pc_doctor_knowledge.db"; DestDir: "{app}"; Flags: onlyifdoesntexist
; 图标文件（可选，如果存在就安装，不存在也不报错）
Source: "pc_doctor.ico"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
; 开始菜单快捷方式（显式指定图标文件）
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\pc_doctor.ico"
; 桌面快捷方式（根据用户选择）
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\pc_doctor.ico"; Tasks: desktopicon
; 卸载快捷方式
Name: "{group}\卸载 电脑医生"; Filename: "{uninstallexe}"

[Run]
; 安装完成后自动启动程序（可选）
Filename: "{app}\{#MyAppExeName}"; Description: "启动 电脑医生"; Flags: nowait postinstall skipifsilent