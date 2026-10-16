# Builds the tester kit: the guide, the app controller, the build benchmark and the standard benchmark runner.
#   powershell -ExecutionPolicy Bypass -File packaging\make_tester_kit.ps1
# Output: packaging\out\zen-harness-tester-kit-<version>.zip  (unzip anywhere; see TESTER_GUIDE.md inside)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Version = (Get-Content (Join-Path $Root "VERSION") -Raw).Trim()
$Be = Join-Path $Root "backend"
$Stage = Join-Path $PSScriptRoot "out\kit\zen-harness-tester-kit"
if (Test-Path $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force }
New-Item -ItemType Directory -Force (Join-Path $Stage "build"), (Join-Path $Stage "standard") | Out-Null

Copy-Item (Join-Path $Root "docs\TESTER_GUIDE.md") $Stage
Copy-Item (Join-Path $Be "evals\zenctl.py") $Stage
Copy-Item (Join-Path $Be "model_profiles.json") $Stage            # the runner's model profiles
foreach ($f in "SPEC.md", "RUBRIC.md", "GRADES.md", "run_build_in_app.py") {
    Copy-Item (Join-Path $Be "evals\build\$f") (Join-Path $Stage "build")
}
foreach ($f in "README.md", "run.py", "serve.py", "suites.py", "mock_server.py", "requirements.txt", "setup.ps1") {
    Copy-Item (Join-Path $Be "evals\standard\$f") (Join-Path $Stage "standard")
}
Set-Content -LiteralPath (Join-Path $Stage "VERSION.txt") -Value "Zen tester kit for $Version" -Encoding ascii

$Zip = Join-Path $PSScriptRoot "out\zen-harness-tester-kit-$Version.zip"
if (Test-Path $Zip) { Remove-Item -LiteralPath $Zip -Force }
Compress-Archive -Path $Stage -DestinationPath $Zip
Write-Host "Tester kit: $Zip"
