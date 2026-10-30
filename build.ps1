param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Version = (Get-Content -LiteralPath (Join-Path $Root "VERSION") -Raw).Trim()
$DistDir = Join-Path $Root "dist"
$BuildDir = Join-Path $Root "build"
$ReleaseDir = Join-Path $Root "release"
$PackageName = "LanMacFinder-Windows-x64-$Version"
$PackageDir = Join-Path $ReleaseDir $PackageName
$ExePath = Join-Path $ReleaseDir "LanMacFinder.exe"

Set-Location -LiteralPath $Root

$PythonCommand = if ($env:LANMAC_PYTHON) { $env:LANMAC_PYTHON } else { "py" }
$PythonPrefix = if ([IO.Path]::GetFileNameWithoutExtension($PythonCommand) -eq "py") { @("-3") } else { @() }

& $PythonCommand @PythonPrefix tools\generate_icon.py
if ($LASTEXITCODE -ne 0) {
    throw "Icon generation failed"
}

if (-not $SkipTests) {
    & $PythonCommand @PythonPrefix -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        throw "Tests failed"
    }
    & $PythonCommand @PythonPrefix -m py_compile launcher.py src\lanmacfinder\__init__.py src\lanmacfinder\server.py src\lanmacfinder\windows_network.py
    if ($LASTEXITCODE -ne 0) {
        throw "Python compilation failed"
    }
    if (Get-Command node -ErrorAction SilentlyContinue) {
        & node --check src\frontend\csv.js
        if ($LASTEXITCODE -ne 0) {
            throw "csv.js syntax check failed"
        }
        & node --check src\frontend\app.js
        if ($LASTEXITCODE -ne 0) {
            throw "app.js syntax check failed"
        }
        & node --test tests\test_csv_contract.js
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend CSV tests failed"
        }
    } else {
        Write-Warning "Node.js is unavailable; JavaScript checks were skipped."
    }
}

foreach ($path in @($DistDir, $BuildDir, $PackageDir)) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
    }
}
New-Item -ItemType Directory -Path $DistDir, $BuildDir, $PackageDir -Force | Out-Null

& $PythonCommand @PythonPrefix -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name LanMacFinder `
    --icon (Join-Path $Root "assets\lan-mac-finder.ico") `
    --version-file (Join-Path $Root "version_info.txt") `
    --paths (Join-Path $Root "src") `
    --add-data "$(Join-Path $Root 'src\frontend');frontend" `
    --distpath $DistDir `
    --workpath $BuildDir `
    --specpath $BuildDir `
    launcher.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed"
}

$BuiltExe = Join-Path $DistDir "LanMacFinder.exe"
if (-not (Test-Path -LiteralPath $BuiltExe)) {
    throw "Built executable was not found"
}
Copy-Item -LiteralPath $BuiltExe -Destination $ExePath -Force

$SelfTestResult = Join-Path $ReleaseDir "selftest.json"
$Process = Start-Process `
    -FilePath $ExePath `
    -ArgumentList @("--selftest", "--result-file", $SelfTestResult) `
    -Wait `
    -PassThru
if ($Process.ExitCode -ne 0) {
    throw "EXE self-test failed with exit code $($Process.ExitCode)"
}
$SelfTest = Get-Content -LiteralPath $SelfTestResult -Raw -Encoding UTF8 | ConvertFrom-Json
if ($SelfTest.status -ne "ok") {
    throw "EXE self-test returned status $($SelfTest.status)"
}

Copy-Item -LiteralPath $ExePath -Destination (Join-Path $PackageDir "LanMacFinder.exe") -Force
Copy-Item -LiteralPath (Join-Path $Root "README.md") -Destination $PackageDir -Force
Copy-Item -LiteralPath (Join-Path $Root "LICENSE") -Destination $PackageDir -Force
Copy-Item -LiteralPath $SelfTestResult -Destination (Join-Path $PackageDir "selftest.json") -Force

$ExeHash = (Get-FileHash -LiteralPath $ExePath -Algorithm SHA256).Hash.ToLowerInvariant()
"$ExeHash  LanMacFinder.exe" | Set-Content -LiteralPath (Join-Path $ReleaseDir "LanMacFinder.exe.sha256") -Encoding ascii
Copy-Item -LiteralPath (Join-Path $ReleaseDir "LanMacFinder.exe.sha256") -Destination $PackageDir -Force

$ZipPath = Join-Path $ReleaseDir "$PackageName.zip"
if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
Compress-Archive -LiteralPath $PackageDir -DestinationPath $ZipPath -CompressionLevel Optimal
$ZipHash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
"$ZipHash  $(Split-Path $ZipPath -Leaf)" | Set-Content -LiteralPath "$ZipPath.sha256" -Encoding ascii

[pscustomobject]@{
    Version = $Version
    Exe = $ExePath
    ExeBytes = (Get-Item -LiteralPath $ExePath).Length
    ExeSha256 = $ExeHash
    Zip = $ZipPath
    ZipBytes = (Get-Item -LiteralPath $ZipPath).Length
    ZipSha256 = $ZipHash
    SelfTestNetworks = $SelfTest.network_count
} | Format-List
