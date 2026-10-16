# Builds the Zen installer: UI -> licences -> frozen app (PyInstaller) -> llama.cpp -> setup.exe (Inno Setup).
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1 [-SkipUi] [-SkipFreeze] [-LlamaDir C:\path\to\llama.cpp]
# Output: packaging\out\ZenHarness-Setup-<version>.exe (+ .sha256)
param(
    [switch]$SkipUi,
    [switch]$SkipFreeze,
    [string]$LlamaDir = $(if ($env:LLAMA_CPP_DIR) { $env:LLAMA_CPP_DIR } else { Join-Path $env:USERPROFILE "llama.cpp" })
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Out = Join-Path $PSScriptRoot "out"
$Py = Join-Path $Root "backend\venv\Scripts\python.exe"
$Version = (Get-Content (Join-Path $Root "VERSION") -Raw).Trim()
New-Item -ItemType Directory -Force $Out | Out-Null
Write-Host "== Zen $Version"

if (-not (Test-Path $Py)) { throw "backend\venv missing: py -3.13 -m venv backend\venv; backend\venv\Scripts\pip install -r backend\requirements-dev.txt" }
if (-not (Test-Path (Join-Path $LlamaDir "llama-server.exe"))) { throw "llama-server.exe not found in $LlamaDir (set -LlamaDir or LLAMA_CPP_DIR)" }

# 1. UI -> backend\web_dist
if (-not $SkipUi) {
    Push-Location (Join-Path $Root "ui")
    if (-not (Test-Path "node_modules")) { npm ci --no-audit --no-fund; if ($LASTEXITCODE) { throw "npm ci failed" } }
    npm run build; if ($LASTEXITCODE) { throw "UI build failed" }
    Pop-Location
}

# 2. third-party licences -> backend\licenses
& $Py (Join-Path $PSScriptRoot "gen_licenses.py") --llama $LlamaDir
if ($LASTEXITCODE) { throw "licence generation failed" }

# 3. Windows version resource for the exes
$num = ($Version -replace '[^0-9.].*$', '').Split('.') + @('0', '0', '0', '0') | Select-Object -First 4
$tuple = ($num -join ', ')
$versionText = @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($tuple), prodvers=($tuple), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Zen'), StringStruct('FileDescription', 'Zen'),
    StringStruct('FileVersion', '$Version'), StringStruct('ProductName', 'Zen'),
    StringStruct('ProductVersion', '$Version'), StringStruct('LegalCopyright', 'Copyright (c) 2026 the Zen authors')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"@
[IO.File]::WriteAllText((Join-Path $Out "version_info.txt"), $versionText)     # no BOM (PS 5.1 utf8 adds one)

# 4. freeze
$Dist = Join-Path $Out "dist\ZenHarness"
if (-not $SkipFreeze) {
    & $Py -m PyInstaller (Join-Path $PSScriptRoot "zen-harness.spec") --noconfirm --clean `
        --distpath (Join-Path $Out "dist") --workpath (Join-Path $Out "work")
    if ($LASTEXITCODE) { throw "PyInstaller failed" }
}

# 5. llama.cpp: the server, its libraries, the CUDA runtime and the CPU backends (no other tools, no archives)
$LlamaOut = Join-Path $Dist "llama"
New-Item -ItemType Directory -Force $LlamaOut | Out-Null
$keep = @("llama-server.exe", "llama-server-impl.dll", "llama-common.dll", "llama.dll", "mtmd.dll", "libomp.dll",
          "cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll", "LICENSE-LLVM-OpenMP", "LICENSE")
Get-ChildItem $LlamaDir -File | Where-Object { $keep -contains $_.Name -or ($_.Name -like "ggml*.dll" -and $_.Name -ne "ggml-rpc.dll") } |
    ForEach-Object { Copy-Item $_.FullName $LlamaOut -Force }

# 6. smoke test of the frozen program
$cli = Join-Path $Dist "zenharness-cli.exe"
$v = & $cli --version
if ($LASTEXITCODE -or -not ($v -match [regex]::Escape($Version))) { throw "frozen smoke test failed: $v" }
Write-Host "frozen app reports: $v"

# 7. installer
$Iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
& $Iscc "/DAppVersion=$Version" "/DDistDir=$Dist" "/DOutDir=$Out" (Join-Path $PSScriptRoot "zen-harness.iss")
if ($LASTEXITCODE) { throw "Inno Setup failed" }
$Setup = Join-Path $Out "ZenHarness-Setup-$Version.exe"
$hash = (Get-FileHash $Setup -Algorithm SHA256).Hash.ToLower()
"$hash  ZenHarness-Setup-$Version.exe" | Set-Content -Encoding ascii "$Setup.sha256"
Write-Host ("== done: {0} ({1:N0} MB) sha256 {2}" -f $Setup, ((Get-Item $Setup).Length / 1MB), $hash)
