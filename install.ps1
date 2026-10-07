param([switch]$NoPath)
$ErrorActionPreference = 'Stop'
$screenInstall = $PSScriptRoot
$screenRuntime = Join-Path $screenInstall '.runtime'
$screenPython = Join-Path $screenRuntime 'Scripts\python.exe'
$screenBasePython = (Get-Command python -ErrorAction Stop).Source
& $screenBasePython -c "import struct,sys; assert sys.version_info >= (3,9), 'Python 3.9+ required'; assert struct.calcsize('P')==8, '64-bit Python required'"
if ($LASTEXITCODE -ne 0) { throw 'Install a supported 64-bit Python first.' }
if (!(Test-Path -LiteralPath $screenPython)) {
    & $screenBasePython -m venv $screenRuntime
    if ($LASTEXITCODE -ne 0) { throw 'Creating Python runtime failed. Install Python 3.9+ first.' }
}
& $screenPython -m pip install --only-binary=:all: -r (Join-Path $screenInstall 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. See pip output.' }
$screenCompiler = Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (!(Test-Path -LiteralPath $screenCompiler)) {
    $screenCompiler = Join-Path $env:SystemRoot 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (Test-Path -LiteralPath $screenCompiler) {
    & $screenCompiler /nologo /target:exe ("/out:" + (Join-Path $screenInstall 'screen.exe')) (Join-Path $screenInstall 'launcher.cs')
    if ($LASTEXITCODE -ne 0) { throw 'Native launcher compilation failed.' }
} elseif (!(Test-Path -LiteralPath (Join-Path $screenInstall 'screen.exe'))) {
    throw 'Native launcher is missing. Use the ZIP release or install the .NET Framework compiler.'
}
if (!$NoPath) {
    $screenExisting = [Environment]::GetEnvironmentVariable('Path', 'User')
    $screenEntries = @($screenExisting -split ';' | Where-Object { $_ })
    if ($screenEntries -notcontains $screenInstall) {
        [Environment]::SetEnvironmentVariable('Path', (($screenEntries + $screenInstall) -join ';'), 'User')
    }
    $env:Path = $env:Path + ';' + $screenInstall
}
Write-Host 'WinScreen installed. With -NoPath, use the launcher by its full path.' -ForegroundColor Green
Write-Host 'Otherwise open a NEW terminal, then use: screen -S train'
Write-Host 'Dashboard: screen --panel'
Write-Host 'This installer does not modify VS Code or SSH settings.'
