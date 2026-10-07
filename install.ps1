#requires -Version 5.1

<#
    Installation de Python et de la dependance du projet, via WinGet.

        .\install.ps1

    WinGet est le chemin suivi par Windows pour installer un logiciel, donc
    rien a telecharger ni a autoriser dans un pare-feu : WinGet demande
    lui-meme l'elevation quand il en a besoin. Le script n'exige donc pas
    d'etre lance en administrateur.

    3.13 est la version visee par le projet (.github/workflows/ci.yml,
    mypy.ini). Le script ne touche pas a une installation existante : il
    signale seulement si elle n'est pas de la bonne version.

        .\install.ps1 -SkipDependencies    # uniquement Python
#>

param(
    # N'installe pas requests, pour remettre Python sans toucher a l'env.
    [switch]$SkipDependencies
)

$ErrorActionPreference = "Stop"

# La console Windows n'emet pas de l'UTF-8 par defaut : le "e" accentue du
# message de fin s'afficherait de travers. On force la sortie en UTF-8, ce que
# Windows 10 et 11 gerent correctement.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$PackageId = "Python.Python.3.13"
$ProjectRoot = $PSScriptRoot
$Requirements  = Join-Path $ProjectRoot "requirements.txt"

# 0x8A15002B : le paquet est deja installe et aucune mise a jour n'existe.
# Winget le rend sur un "install" qui n'a rien a faire, ce qui n'est pas un echec.
$AlreadyThere = -1978335189

function Info($msg)    { Write-Host $msg -ForegroundColor Cyan }
function Step($msg)    { Write-Host "-> $msg" -ForegroundColor Yellow }
function Ok($msg)      { Write-Host "OK  $msg" -ForegroundColor Green }
function Warn($msg)    { Write-Host "!!  $msg" -ForegroundColor Yellow }
function Die($msg)     { Write-Host "XX  $msg" -ForegroundColor Red; exit 1 }

# Execute une commande native en capturant stdout et stderr, sans que la
# preference d'erreur du script transforme une sortie normale en exception.
function Read-Native($exe, $exeArgs) {
    $ErrorActionPreference = "Continue"
    $out = & $exe @exeArgs 2>&1
    return (($out | ForEach-Object { "$_" }) -join "`n")
}

# ------------------------------------------------------------------ WinGet

$winget = Get-Command winget -ErrorAction SilentlyContinue
if (-not $winget) {
    Write-Host ""
    Die ("WinGet n'est pas disponible." +
         "`nInstalle ou mets a jour 'App Installer' depuis le Microsoft Store," +
         " puis relance ce script.")
}
Ok "WinDetecte ($($winget.Source))"

# ------------------------------------------------------------------ Python deja la

# Winget sait ce qui est installe : c'est plus sur que de sonder
# "python --version", car "python" designe souvent l'alias Microsoft Store,
# qui ouvre le magasin au lieu de repondre.
$installed = Read-Native $winget.Source @(
    "list", "--id", $PackageId, "--exact", "--source", "winget",
    "--accept-source-agreements"
)
$alreadyInstalled = $installed -match [regex]::Escape($PackageId)

# L'alias Store n'est pas un interpreteur : on l'ecarte.
function Find-RealPython {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        $version = Read-Native $launcher.Source @("-3", "--version")
        if ($version -match "Python (\d+)\.(\d+)") { return $launcher.Source }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python -and $python.Source -notlike "*\Microsoft\WindowsApps\*") {
        $version = Read-Native $python.Source @("--version")
        if ($version -match "Python (\d+)\.(\d+)") { return $python.Source }
    }
    return $null
}

$pythonExe = Find-RealPython

Write-Host ""
if ($alreadyInstalled -and $pythonExe) {
    $version = Read-Native $pythonExe @("--version")
    Ok "Python deja installe via WinGet ($version)"
} elseif ($pythonExe) {
    $version = Read-Native $pythonExe @("--version")
    Ok "Python deja installe ($version)"
    if ($version -notmatch "Python 3\.13") {
        Warn "Le projet vise 3.13 (CI et mypy). La suite peut ne pas passer."
    }
} else {
    Step "Installation de Python 3.13 via WinGet"

    # Pas d'elevation forcee ici : WinGet la demande lui-meme quand il en a
    # besoin, ce qui evite d'imposer PowerShell en administrateur.
    Read-Native $winget.Source @(
        "install",
        "--id", $PackageId,
        "--exact",
        "--source", "winget",
        "--accept-source-agreements",
        "--accept-package-agreements"
    ) | Out-Null

    $code = $LASTEXITCODE
    if ($code -ne 0 -and $code -ne $AlreadyThere) {
        Die ("L'installation a echoue (code $code). Relance a la main pour voir" +
             " le message : winget install --id $PackageId --exact --source winget")
    }
    if ($code -eq $AlreadyThere) {
        Ok "Python 3.13 est deja present, rien a installer"
    } else {
        Ok "Python installe"
    }

    # Le PATH de la session ne voit pas l'installation : on va chercher
    # l'executable a son emplacement connu plutot que d'y croire.
    $pythonExe = Find-RealPython
    if (-not $pythonExe) {
        Write-Host ""
        Warn "Python est installe mais introuvable dans cette session."
        Warn "Ferme et rouvre PowerShell, puis relance : .\install.ps1"
        Write-Host ""
        Write-Host "Une fois ouvert, lance le programme avec :" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "    python serveur.py" -ForegroundColor White
        Write-Host ""
        exit 0
    }
}

# -------------------------------------------------------------- dependance

Write-Host ""
if ($SkipDependencies) {
    Info "Dependances ignorees (-SkipDependencies)."
} elseif (-not (Test-Path $Requirements)) {
    Warn ("requirements.txt introuvable a $Requirements :" +
          " dependance non installee.")
} else {
    Step "Installation des dependances du projet (requirements.txt)"
    Read-Native $pythonExe @("-m", "pip", "install", "-r", $Requirements) |
        Out-Null
    if ($LASTEXITCODE -ne 0) {
        Die "Echec de l'installation des dependances (code $LASTEXITCODE)."
    }
    Ok "Dependances installees."
}

# ------------------------------------------------------------------ finale

$command = "python"
if ($pythonExe -and $pythonExe -like "*\Microsoft\WindowsApps\*") {
    $command = "py -3"
}
if ($pythonExe -and $pythonExe -match "Launcher\\py\.exe$") {
    $command = "py -3"
}

Write-Host ""
Write-Host "Python correctement installe, tapez 'serveur.py' pour lancer le logiciel" -ForegroundColor Green
Write-Host ""
Write-Host "La commande complete, dans ce dossier :" -ForegroundColor Cyan
Write-Host ""
Write-Host "    $command serveur.py" -ForegroundColor White
Write-Host ""
Write-Host "Le navigateur s'ouvre tout seul sur http://localhost:8123." -ForegroundColor Cyan
Write-Host ""
