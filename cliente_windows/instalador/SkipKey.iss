; ─────────────────────────────────────────────────────────────────────────────
; Instalador de SkipKey para Windows — Inno Setup 6 o superior (probado con 7)
;
; No se ejecuta a mano: lo genera `crear_instalador.bat`, que compila el
; cliente y produce `salida\SkipKey_Instalador_<versión>.exe`.
;
; Características, que son las que importan al usuario:
;   · Se instala SIN administrador, en %LOCALAPPDATA%\Programs\SkipKey (esa
;     carpeta la puede escribir el cliente: ahí van `comandos_windows.json` y
;     `logs\`, cosa que no ocurriría en C:\Program Files).
;   · Cierra el cliente antes de copiar (el micrófono y el icono quedan libres).
;   · Conserva la configuración y los registros al actualizar.
;   · Registra el arranque con Windows ejecutando `ClienteVoz.exe --instalar`,
;     y lo quita al desinstalar.
;   · La clave del proxy NO se pide: el .exe la lleva incrustada. Si además
;     existe `clave_proxy.txt`, se copia junto al exe como respaldo.
; ─────────────────────────────────────────────────────────────────────────────

#ifndef Version
  #define Version "1.0.0"
#endif

#define MiApp "SkipKey"
#define MiExe "ClienteVoz.exe"
#define CarpetaCliente ".."

[Setup]
AppId={{7E2C4B9A-5D3F-4F8E-9A61-2B7C0F5D1A34}
AppName={#MiApp}
AppVersion={#Version}
AppVerName={#MiApp} {#Version}
AppPublisher=SkipKey
DefaultDirName={localappdata}\Programs\{#MiApp}
DefaultGroupName={#MiApp}
DisableProgramGroupPage=yes
DisableWelcomePage=no
PrivilegesRequired=lowest
OutputDir=salida
OutputBaseFilename=SkipKey_Instalador_{#Version}
SetupIconFile=icono.ico
UninstallDisplayIcon={app}\{#MiExe}
UninstallDisplayName={#MiApp}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; El cliente es una app de bandeja: no secuestra la atención al instalar.
ShowLanguageDialog=no

[Languages]
Name: "espanol"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "escritorio"; Description: "Crear un acceso directo en el escritorio"; Flags: unchecked

[Files]
; El binario del cliente (lleva Python, dependencias y la clave del proxy).
Source: "{#CarpetaCliente}\{#MiExe}"; DestDir: "{app}"; Flags: ignoreversion
; Configuración: solo se crea si no existía (una actualización no la pisa) y se
; borra al desinstalar (lleva la clave del proxy y los ajustes de esa PC).
Source: "{#CarpetaCliente}\comandos_windows.json"; DestDir: "{app}"; Flags: onlyifdoesntexist
; Respaldo de la clave y la guía (opcionales).
Source: "{#CarpetaCliente}\clave_proxy.txt"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#CarpetaCliente}\README_windows.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
; Versión del cliente (`ClienteVoz.exe --version` y el registro de arranque).
; La escribe crear_instalador.bat antes de llamar a Inno Setup.
Source: "{#CarpetaCliente}\version.txt"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MiApp}"; Filename: "{app}\{#MiExe}"
Name: "{group}\Configuración de {#MiApp}"; Filename: "{app}\{#MiExe}"; Parameters: "--configuracion"
Name: "{group}\Desinstalar {#MiApp}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MiApp}"; Filename: "{app}\{#MiExe}"; Tasks: escritorio
; Arranque al iniciar sesión: acceso directo en la carpeta Inicio del usuario.
; Inno lo quita solo al desinstalar. No se usa `schtasks`, que falla sin
; elevación y dejó al cliente sin arrancar con Windows (2026-11-19).
Name: "{userstartup}\{#MiApp}"; Filename: "{app}\{#MiExe}"

[Run]
; El arranque al iniciar sesión lo pone el acceso de `{userstartup}` de arriba:
; no hay que ejecutar `--instalar` (y `schtasks` fallaba sin administrador).
Filename: "{app}\{#MiExe}"; Description: "Abrir {#MiApp} ahora"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#MiExe}"; Parameters: "--desinstalar --silencioso"; Flags: runhidden waituntilterminated; RunOnceId: "QuitarArranque"

[UninstallDelete]
Type: files; Name: "{app}\comandos_windows.json"
Type: files; Name: "{app}\clave_proxy.txt"
Type: filesandordirs; Name: "{app}\logs"
Type: dirifempty; Name: "{app}"

[Code]
{ El cliente corre en la bandeja: hay que cerrarlo antes de copiar (o de
  borrar) sus archivos. No se avisa si no estaba abierto. }
procedure MatarCliente();
var
  Codigo: Integer;
begin
  Exec('taskkill.exe', '/F /IM {#MiExe}', '', SW_HIDE, ewWaitUntilTerminated, Codigo);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  MatarCliente();
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    MatarCliente();
end;
