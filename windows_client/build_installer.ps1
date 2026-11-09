$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$publish = Join-Path $root "publish\WarehouseClient-win-x64"
$payloadDir = Join-Path $root "installer\Payload"
$payload = Join-Path $payloadDir "WarehouseClientPayload.zip"
$installerPublish = Join-Path $root "installer\publish"
$installerExe = Join-Path $root "WarehouseClientInstaller.exe"

foreach ($path in @($publish, $installerPublish)) {
    $rootFull = [System.IO.Path]::GetFullPath($root).TrimEnd('\') + '\'
    $pathFull = [System.IO.Path]::GetFullPath($path)
    if (-not $pathFull.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove outside client root: $path"
    }
    if (Test-Path $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
    }
}

New-Item -ItemType Directory -Force -Path $publish, $payloadDir, $installerPublish | Out-Null
if (Test-Path $payload) {
    Remove-Item -LiteralPath $payload -Force
}

dotnet publish (Join-Path $root "WarehouseClient\WarehouseClient.csproj") -c Release -r win-x64 --self-contained true -p:PublishSingleFile=false -o $publish
Copy-Item -LiteralPath (Join-Path $root "README.md") -Destination (Join-Path $publish "README.md") -Force
Compress-Archive -Path (Join-Path $publish "*") -DestinationPath $payload -Force

dotnet publish (Join-Path $root "installer\WarehouseClientSetup\WarehouseClientSetup.csproj") -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -o $installerPublish
Copy-Item -LiteralPath (Join-Path $installerPublish "WarehouseClientSetup.exe") -Destination $installerExe -Force

Get-Item $installerExe
