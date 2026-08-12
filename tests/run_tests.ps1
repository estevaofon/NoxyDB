param(
    [string]$Test = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$noxyExe = $env:NOXY_EXE

if ([string]::IsNullOrWhiteSpace($noxyExe)) {
    $noxyExe = (Resolve-Path (Join-Path $projectRoot "..\..\go_projects\noxy\noxy.exe")).Path
}

if (-not (Test-Path -LiteralPath $noxyExe -PathType Leaf)) {
    throw "Noxy executable not found: $noxyExe"
}

$tests = if ([string]::IsNullOrWhiteSpace($Test)) {
    @("database_test.nx", "write_failure_test.nx", "close_failure_test.nx")
} else {
    @($Test)
}

Push-Location $projectRoot
try {
    foreach ($testFile in $tests) {
        $testPath = Join-Path "tests" $testFile
        & $noxyExe $testPath
        if ($LASTEXITCODE -ne 0) {
            throw "Noxy test failed: $testFile (exit code $LASTEXITCODE)"
        }
    }
} finally {
    Pop-Location
}

Write-Output "All NoxyDB tests passed ($($tests.Count) files)."
