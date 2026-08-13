param(
    [string]$Test = "",
    [string]$Group = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$noxyExe = $env:NOXY_EXE

if ([string]::IsNullOrWhiteSpace($noxyExe)) {
    throw "Set NOXY_EXE to a Noxy executable that includes io.write_result and io.close_result"
}

if (-not (Test-Path -LiteralPath $noxyExe -PathType Leaf)) {
    throw "Noxy executable not found: $noxyExe"
}

$coreTests = @(
    "database_test.nx",
    "document_codec_test.nx",
    "document_isolation_test.nx",
    "write_failure_test.nx",
    "close_failure_test.nx"
)
$persistenceTests = @(
    "persistence_write_test.nx",
    "persistence_read_test.nx",
    "deleted_write_test.nx",
    "deleted_read_test.nx",
    "history_write_test.nx",
    "history_read_test.nx",
    "empty_database_test.nx"
)
$errorTests = @(
    "invalid_log_test.nx",
    "invalid_document_log_test.nx",
    "open_failure_test.nx",
    "read_size_test.nx"
)
$serverTests = @(
    "server_protocol_test.nx"
)

$tests = if (-not [string]::IsNullOrWhiteSpace($Test)) {
    @($Test)
} elseif ($Group -eq "persistence") {
    $persistenceTests
} elseif ($Group -eq "errors") {
    $errorTests
} elseif ($Group -eq "server") {
    $serverTests
} elseif ([string]::IsNullOrWhiteSpace($Group)) {
    $coreTests + $persistenceTests + $errorTests + $serverTests
} else {
    throw "Unknown test group: $Group"
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
