$AppDataPath = [System.Environment]::GetFolderPath('ApplicationData')
$NodePath = Join-Path $AppDataPath "fnm\node-versions\v20.17.0\installation"
$BinPath = Join-Path $NodePath "node_modules\.bin"

$env:Path = "$NodePath;$BinPath;$env:Path"

node -v
npm -v
eslint -v
