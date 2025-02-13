#------------------------------------------------------------------------------
# Open all code in a text editor, with each type of file in a separate window.
#------------------------------------------------------------------------------
Add-Type @"
using System;
using System.Runtime.InteropServices;

public class WinAPI {
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
}
"@

function Press-WinRight {
    $WIN = 0x5B  # Windows (Left) Key
    $RIGHT = 0x27  # Right Arrow Key
    $PRESS = 0
    $RELEASE = 2
    Start-Sleep -Milliseconds 100
    [WinAPI]::keybd_event($WIN, 0, $PRESS, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 50
    [WinAPI]::keybd_event($RIGHT, 0, $PRESS, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 50
    [WinAPI]::keybd_event($RIGHT, 0, $RELEASE, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 50
    [WinAPI]::keybd_event($WIN, 0, $RELEASE, [UIntPtr]::Zero)
}

function OpenAndSnap {
    param ($files)
    if ($files.Count -eq 0) { return }
    $proc = Start-Process $EDITOR -ArgumentList $files -PassThru
    Start-Sleep -Milliseconds 300
    $win = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
    if ($win) {
        $null = [WinAPI]::SetForegroundWindow($win.MainWindowHandle)
        Start-Sleep -Milliseconds 250
        Press-WinRight
    }
}

function Get-FilteredChildItems {
    param ([string]$Path, [string]$Filter)
    Get-ChildItem -Path $Path -Filter $Filter -Recurse | Where-Object { $_.DirectoryName -notlike '*\venv\*' }
}

$EDITOR = "${Env:ProgramFiles}/Vim/vim91/gvim.exe"
$inpath = Join-Path $PSScriptRoot "../app"
$htmlFiles = Get-FilteredChildItems -Path $inpath -Filter "*.html"
$cssFiles = Get-FilteredChildItems -Path $inpath -Filter "*.css"
$pyFiles = Get-FilteredChildItems -Path $inpath -Filter "*.py"
$txtFiles = Get-ChildItem -Path $PSScriptRoot -Filter "*.txt" -File
$mdFiles = Get-ChildItem -Path (Join-Path $PSScriptRoot "..") -Filter "*.md" -File
$jsonFiles = Get-ChildItem -Path (Join-Path $inpath "data_files") -Filter "*.json" -File

# Quote each file path individually
$htmlFilePaths = $htmlFiles.FullName | ForEach-Object { "`"$_`"" }
$cssFilePaths = $cssFiles.FullName | ForEach-Object { "`"$_`"" }
$pyFilePaths = $pyFiles.FullName | ForEach-Object { "`"$_`"" }
$txtFilePaths = $txtFiles.FullName | ForEach-Object { "`"$_`"" }
$mdFilePaths = $mdFiles.FullName | ForEach-Object { "`"$_`"" }
$jsonFilePaths = $jsonFiles.FullName | ForEach-Object { "`"$_`"" }

OpenAndSnap ($htmlFilePaths + $cssFilePaths)
OpenAndSnap $pyFilePaths
OpenAndSnap ($txtFilePaths + $mdFilePaths)
OpenAndSnap $jsonFilePaths
