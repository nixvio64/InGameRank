!define APPNAME "InGameRank"
!define APPEXE "InGameRank.exe"
!define APPICON "InGameRank.ico"

Name "${APPNAME}"
OutFile "${APPNAME}_Installer.exe"
InstallDir "$LOCALAPPDATA\${APPNAME}"


RequestExecutionLevel user

Page directory
Page instfiles

UninstPage uninstConfirm
UninstPage instfiles

Section "Install"
  SetOutPath $INSTDIR
  

  File "${APPEXE}"
  

  File "${APPICON}"
  

  CreateShortCut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\${APPEXE}" "" "$INSTDIR\${APPICON}"

  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortCut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$INSTDIR\${APPEXE}" "" "$INSTDIR\${APPICON}"
  CreateShortCut "$SMPROGRAMS\${APPNAME}\Uninstall ${APPNAME}.lnk" "$INSTDIR\uninstall.exe"
  

  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Section "Uninstall"

  Delete "$INSTDIR\${APPEXE}"
  Delete "$INSTDIR\${APPICON}"
  Delete "$INSTDIR\uninstall.exe"
  

  Delete "$INSTDIR\config.json"
  

  RMDir "$INSTDIR"
  

  Delete "$DESKTOP\${APPNAME}.lnk"
  Delete "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk"
  Delete "$SMPROGRAMS\${APPNAME}\Uninstall ${APPNAME}.lnk"
  RMDir "$SMPROGRAMS\${APPNAME}"
SectionEnd