# One-time setup for the benchmark runner: a separate Python environment outside OneDrive with pinned packages.
#   powershell -ExecutionPolicy Bypass -File setup.ps1
# Then:  & "$env:USERPROFILE\venvs\zen-evals\Scripts\python.exe" run.py --list
$ErrorActionPreference = "Stop"
$Venv = Join-Path $env:USERPROFILE "venvs\zen-evals"
$Here = $PSScriptRoot

# Python 3.11-3.13 via the py launcher (python.org installer), else whatever "python" is
$py = $null
foreach ($v in "3.13", "3.12", "3.11") {
    try { & py "-$v" -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $py = @("py", "-$v"); break } } catch {}
}
if (-not $py) { $py = @("python") }
& $py[0] $py[1..9] -c "import sys; assert (3, 11) <= sys.version_info[:2] <= (3, 13), sys.version" 2>$null
if ($LASTEXITCODE) { throw "Python 3.11-3.13 is required (python.org installer, with the py launcher)" }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "git is required (git-scm.com) - BFCL downloads its data with it" }

if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
    Write-Host "Creating $Venv"
    & $py[0] $py[1..9] -m venv $Venv
}
$vpy = Join-Path $Venv "Scripts\python.exe"
& $vpy -m pip install --upgrade pip --quiet
& $vpy -m pip install -r (Join-Path $Here "requirements.txt")
if ($LASTEXITCODE) { throw "pip install failed" }
Write-Host ""
Write-Host "Ready. Next:"
Write-Host "  & `"$vpy`" `"$(Join-Path $Here 'run.py')`" --list"
