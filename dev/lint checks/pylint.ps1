#
# Install into the venv if not yet done: pip install pylint
# To generate .pylintrc: pylint --generate-rcfile | out-file -encoding utf8 .pylintrc
#
# Then run for example: pylint src.item
#
$scriptDir = Split-Path -Path $MyInvocation.MyCommand.Definition -Parent
$appDir = Join-Path -Path $scriptDir -ChildPath ".."
$workingDir = Join-Path -Path $appDir -ChildPath "app"
$workingDir = $workingDir.Replace(' ', '` ')
$pylintrc = Join-Path -Path $scriptDir -ChildPath "pylintrc"

# Open a new PowerShell window
$cmds = @(
    "`$env:PYLINTRC='$pylintrc'",
    "cd `"$workingDir`"",
    "./venv/Scripts/activate"
)
$command = $cmds -join "; "
echo $command
Start-Process powershell -ArgumentList "-NoExit", "-Command", $command
