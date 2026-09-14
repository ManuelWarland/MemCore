Set objShell = CreateObject("WScript.Shell")
objShell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""C:\Users\wmlog\MemCore\scripts\kill_orphaned_memcore.ps1""", 0, False
