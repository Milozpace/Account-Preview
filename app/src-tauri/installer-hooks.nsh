; Display-only rename. PRODUCTNAME remains the legacy installation key/path,
; so an existing Preview install never adopts personal Account's registry keys.
!macro AccountRenameShortcut source destination fallback directory
  Push $0
  Push $1
  Push $2
  Push $3
  !insertmacro IsShortcutTarget "${source}" "$INSTDIR\${MAINBINARYNAME}.exe"
  Pop $0
  ${If} $0 = 1
    ${If} ${FileExists} "${destination}"
      !insertmacro IsShortcutTarget "${destination}" "$INSTDIR\${MAINBINARYNAME}.exe"
      Pop $0
      ${If} $0 = 1
        Delete "${source}"
      ${Else}
        ; A personal Account (or another app) already owns this shortcut.
        ; Preserve it and put the Preview shortcut in its own directory.
        ${If} ${FileExists} "${fallback}"
          !insertmacro IsShortcutTarget "${fallback}" "$INSTDIR\${MAINBINARYNAME}.exe"
          Pop $0
          ${If} $0 = 1
            Delete "${source}"
          ${EndIf}
        ${Else}
          CreateDirectory "${directory}"
          Rename "${source}" "${fallback}"
        ${EndIf}
      ${EndIf}
    ${Else}
      Rename "${source}" "${destination}"
    ${EndIf}
  ${EndIf}
  Pop $3
  Pop $2
  Pop $1
  Pop $0
!macroend

!macro AccountRemoveShortcut shortcut
  Push $0
  Push $1
  Push $2
  Push $3
  !insertmacro IsShortcutTarget "${shortcut}" "$INSTDIR\${MAINBINARYNAME}.exe"
  Pop $0
  ${If} $0 = 1
    !insertmacro UnpinShortcut "${shortcut}"
    Delete "${shortcut}"
  ${EndIf}
  Pop $3
  Pop $2
  Pop $1
  Pop $0
!macroend

!macro NSIS_HOOK_POSTINSTALL
  WriteRegStr SHCTX "${UNINSTKEY}" "DisplayName" "Account"
  !if "${BUNDLEID}" == "com.account.preview"
    !insertmacro AccountRenameShortcut "$DESKTOP\${PRODUCTNAME}.lnk" "$DESKTOP\Account.lnk" "$DESKTOP\Account-Preview\Account.lnk" "$DESKTOP\Account-Preview"
    !insertmacro AccountRenameShortcut "$SMPROGRAMS\${PRODUCTNAME}.lnk" "$SMPROGRAMS\Account.lnk" "$SMPROGRAMS\Account-Preview\Account.lnk" "$SMPROGRAMS\Account-Preview"
    !if "${STARTMENUFOLDER}" != ""
      !insertmacro AccountRenameShortcut "$SMPROGRAMS\$AppStartMenuFolder\${PRODUCTNAME}.lnk" "$SMPROGRAMS\$AppStartMenuFolder\Account.lnk" "$SMPROGRAMS\Account-Preview\Account.lnk" "$SMPROGRAMS\Account-Preview"
    !endif
  !endif
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !if "${BUNDLEID}" == "com.account.preview"
    !insertmacro AccountRemoveShortcut "$DESKTOP\Account.lnk"
    !insertmacro AccountRemoveShortcut "$DESKTOP\Account-Preview\Account.lnk"
    !insertmacro AccountRemoveShortcut "$SMPROGRAMS\Account.lnk"
    !insertmacro AccountRemoveShortcut "$SMPROGRAMS\Account-Preview\Account.lnk"
    !if "${STARTMENUFOLDER}" != ""
      !insertmacro AccountRemoveShortcut "$SMPROGRAMS\$AppStartMenuFolder\Account.lnk"
    !endif
    RMDir "$DESKTOP\Account-Preview"
    RMDir "$SMPROGRAMS\Account-Preview"
  !endif
!macroend
