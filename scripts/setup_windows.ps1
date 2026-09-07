[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LockFile = Join-Path $ProjectRoot "requirements\windows-py313.lock"

function Test-Python313 {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [string[]]$PrefixArguments = @()
    )

    try {
        & $Executable @PrefixArguments -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) and sys.maxsize > 2**32 else 1)" 2>$null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Find-Python313 {
    if ($env:STEPINBEL_PYTHON) {
        if (Test-Python313 -Executable $env:STEPINBEL_PYTHON) {
            return @{ Executable = $env:STEPINBEL_PYTHON; Arguments = @() }
        }
        throw "STEPINBEL_PYTHON does not point to 64-bit Python 3.13: $env:STEPINBEL_PYTHON"
    }

    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher -and (Test-Python313 -Executable $launcher.Source -PrefixArguments @("-3.13"))) {
        return @{ Executable = $launcher.Source; Arguments = @("-3.13") }
    }

    foreach ($name in @("python3.13", "python")) {
        $candidate = Get-Command $name -ErrorAction SilentlyContinue
        if ($candidate -and (Test-Python313 -Executable $candidate.Source)) {
            return @{ Executable = $candidate.Source; Arguments = @() }
        }
    }

    throw @"
64-bit Python 3.13 was not found.

Install Python 3.13, reopen this folder, and run setup.cmd again.
If Python 3.13 is installed in a custom location, set STEPINBEL_PYTHON to the
full path of python.exe before running setup.cmd.
"@
}

Set-Location $ProjectRoot
$bootstrap = Find-Python313

if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host "Creating .venv with Python 3.13"
    & $bootstrap.Executable @($bootstrap.Arguments) -m venv (Join-Path $ProjectRoot ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Python could not create .venv."
    }
}

if (-not (Test-Python313 -Executable $VenvPython)) {
    throw @"
The existing .venv was not created with 64-bit Python 3.13.
Delete only the .venv folder, then run setup.cmd again.
"@
}

if (-not (Test-Path -LiteralPath $LockFile -PathType Leaf)) {
    throw "Dependency lock file not found: $LockFile"
}

Write-Host "Installing the tested Windows dependencies"
& $VenvPython -m pip install --upgrade "pip==26.2.1" "setuptools==80.9.0"
if ($LASTEXITCODE -ne 0) {
    throw "Could not install the Python packaging tools. Check the internet connection."
}

& $VenvPython -m pip install --no-build-isolation --constraint $LockFile ".[ui]"
if ($LASTEXITCODE -ne 0) {
    throw "Could not install the simulator dependencies. Check the message above."
}

Write-Host "Checking the installation"
& $VenvPython (Join-Path $ProjectRoot "scripts\doctor.py")
if ($LASTEXITCODE -ne 0) {
    throw "The installation check failed."
}

Write-Host ""
Write-Host "Setup completed. Start the simulator with start.cmd."
