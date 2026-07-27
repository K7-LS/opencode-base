$ErrorActionPreference = 'Stop'
$Utf8NoBom = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$ConnectionRuntime = Join-Path (
    Split-Path -Parent $PSScriptRoot
) 'connection.ps1'
if (-not (Test-Path -LiteralPath $ConnectionRuntime -PathType Leaf)) {
    exit 0
}
. $ConnectionRuntime

function Write-Utf8NoBom {
    param([string]$Path, [string]$Text)
    $Encoding = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Text, $Encoding)
}

try {
    $BaseHome = Join-Path $env:USERPROFILE '.config\opencode\base'
    $VersionPath = Join-Path $BaseHome 'VERSION'
    if (-not (Test-Path -LiteralPath $VersionPath -PathType Leaf)) { exit 0 }

    $StateRoot = Join-Path $BaseHome 'state'
    $StatePath = Join-Path $StateRoot 'update-check.json'
    $Now = [DateTimeOffset]::UtcNow
    if (Test-Path -LiteralPath $StatePath -PathType Leaf) {
        try {
            $State = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 |
                ConvertFrom-Json
            $Checked = [DateTimeOffset]::Parse([string]$State.checked_at)
            if (($Now - $Checked).TotalHours -lt 24) { exit 0 }
        } catch {
            # A corrupt TTL file is replaced by the next successful check.
        }
    }

    $Releases = Invoke-WithLlmConnection `
        -HomePath $env:USERPROFILE `
        -ScriptBlock {
            Invoke-LlmJsonGet `
                -Uri 'https://api.github.com/repos/daniileliseev1337/opencode-base/releases?per_page=20' `
                -UserAgent 'opencode-base-version-check/1' `
                -TimeoutSeconds 5
        }
    $Stable = @($Releases) |
        Where-Object {
            (-not $_.draft) -and
            (-not $_.prerelease) -and
            ([string]$_.tag_name -match '^opencode-v\d+\.\d+\.\d+$')
        } |
        Sort-Object -Property published_at -Descending |
        Select-Object -First 1

    [IO.Directory]::CreateDirectory($StateRoot) | Out-Null
    $StatePayload = [ordered]@{
        checked_at = $Now.ToString('o')
        latest_tag = if ($Stable) { [string]$Stable.tag_name } else { $null }
    } | ConvertTo-Json -Compress
    Write-Utf8NoBom $StatePath ($StatePayload + "`n")
    if (-not $Stable) { exit 0 }

    $CurrentText = (
        Get-Content -LiteralPath $VersionPath -Raw -Encoding UTF8
    ).Trim()
    $LatestText = ([string]$Stable.tag_name) -replace '^opencode-v', ''
    if ([version]$LatestText -le [version]$CurrentText) { exit 0 }

    Write-Output "OpenCode-base $LatestText is available. Run `$sync-base."
} catch {
    # A notification check must never block a client session.
}
exit 0
