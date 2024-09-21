#------------------------------------------------------------------------------
# Opens all code for editing using a text editor, with each type of file
# in a separate window.
#------------------------------------------------------------------------------
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

# Quote each file path individually
$htmlFilePaths = $htmlFiles.FullName | ForEach-Object { "`"$_`"" }
$cssFilePaths = $cssFiles.FullName | ForEach-Object { "`"$_`"" }
$pyFilePaths = $pyFiles.FullName | ForEach-Object { "`"$_`"" }
$txtFilePaths = $txtFiles.FullName | ForEach-Object { "`"$_`"" }

$combinedHtmlCssPaths = $htmlFilePaths + $cssFilePaths

Start-Process $EDITOR -ArgumentList $combinedHtmlCssPaths
Start-Sleep -Milliseconds 250
Start-Process $EDITOR -ArgumentList $pyFilePaths
Start-Sleep -Milliseconds 250
Start-Process $EDITOR -ArgumentList $txtFilePaths
