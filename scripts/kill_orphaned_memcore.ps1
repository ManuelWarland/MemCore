<#
.SYNOPSIS
    Tue les process memcore_mcp.py orphelins (dont le process parent n'existe plus).

.DESCRIPTION
    Un serveur MCP memcore_mcp.py est lancé en sous-processus par chaque client
    (Claude Code, Codex, Kimi, GLM/Hermes...) pour toute la durée de la connexion
    stdio. Si le process parent meurt de façon abrupte (ex. limite de session CLI
    atteinte, kill brutal) sans fermer proprement le pipe stdio, l'enfant reste
    orphelin — connexion de toute façon inutilisable, mais il peut garder une
    transaction SQLite (WAL) ouverte et bloquer les nouvelles connexions jusqu'à
    busy_timeout (30s).

    Sûr par construction : ne tue QUE les process dont le parent n'existe plus.
    Une connexion active a nécessairement son parent vivant (c'est lui qui lit/
    écrit sur le pipe) — jamais de faux positif sur une écriture en cours.

.NOTES
    Prévu pour tourner en tâche planifiée (toutes les 15 min) ET en hook
    SessionStart Claude Code (nettoyage immédiat à l'ouverture d'une session).
#>

$logPath = "C:\Users\wmlog\MemCore\logs\orphan_kill.log"
New-Item -ItemType Directory -Force -Path (Split-Path $logPath) | Out-Null

function Write-Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logPath -Value "[$ts] $msg"
}

$targets = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*memcore_mcp.py*' }

if (-not $targets) {
    exit 0
}

$killed = 0
foreach ($proc in $targets) {
    $parentAlive = $false
    if ($proc.ParentProcessId) {
        $parentAlive = [bool](Get-Process -Id $proc.ParentProcessId -ErrorAction SilentlyContinue)
    }
    if (-not $parentAlive) {
        try {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
            Write-Log "Orphelin tué : PID $($proc.ProcessId) (parent $($proc.ParentProcessId) introuvable)"
            $killed++
        } catch {
            Write-Log "Échec kill PID $($proc.ProcessId) : $_"
        }
    }
}

if ($killed -eq 0) {
    exit 0
}
