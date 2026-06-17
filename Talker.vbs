' Launch Talker with NO console window.
' Unlike a .bat (which always flashes a cmd window), a .vbs run by wscript.exe
' has no console at all, and pythonw.exe runs the app windowless. Double-click
' this file, or make a Desktop shortcut to it.
Option Explicit
Dim sh, fso, dir, exe, script
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir

' Use pythonw from PATH (windowless). If your Python isn't on PATH, replace
' "pythonw.exe" below with the full path to your pythonw.exe.
exe = "pythonw.exe"

script = dir & "\main.py"

' Run hidden (0) and don't wait — the tray app keeps running on its own.
sh.Run """" & exe & """ """ & script & """", 0, False
