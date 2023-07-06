# vim: set filetype=conf :
#------------------------------------------------------------------------------
#
# Created July 5 2023 by Jim K
#
# Opens all code for editing using a text editor, with each type of file
# in a separate window.
#
#------------------------------------------------------------------------------
function Get-FilteredChildItems {
    param ([string]$Path, [string]$Filter)
    Get-ChildItem -Path $Path -Filter $Filter -Recurse | Where-Object { $_.DirectoryName -notlike '*\venv\*' }
}

$EDITOR = "${Env:ProgramFiles(x86)}\Vim\vim80\gvim.exe"
$inpath = Join-Path $PSScriptRoot "app"
$htmlFiles = Get-FilteredChildItems -Path $inpath -Filter "*.html"
$cssFiles = Get-FilteredChildItems -Path $inpath -Filter "*.css"
$pyFiles = Get-FilteredChildItems -Path $inpath -Filter "*.py"
& $EDITOR $htmlFiles.FullName + $cssFiles.FullName
Start-Sleep -Milliseconds 500
& $EDITOR $pyFiles.FullName

