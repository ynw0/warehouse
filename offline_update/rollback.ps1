param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
$ErrorActionPreference = "Stop"
$Python = if ($env:PYTHON) { $env:PYTHON } else { "python" }
& $Python (Join-Path $PSScriptRoot "rollback.py") @Arguments
exit $LASTEXITCODE
