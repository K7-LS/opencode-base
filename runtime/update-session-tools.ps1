[CmdletBinding()]
param(
    [switch]$ManagedPreflight,
    [string]$TransactionId,
    [string]$StartTick,
    [string]$MutationCutoffTick,
    [string]$KillTick,
    [string]$HardDeadlineTick,
    [string]$StopwatchFrequency
)

$ErrorActionPreference = "Stop"
$script:Target = "opencode"
$script:Repository = "daniileliseev1337/opencode-base"
$script:ResultTag = "none"
$script:InMutation = $false
$script:LockStream = $null
$script:TempRoot = $null

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

if (-not ("Foundation.StrictJson" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;

namespace Foundation
{
    public static class StrictJson
    {
        public static void Validate(string value)
        {
            if (value == null || value.Length == 0 || value[0] == '\ufeff')
                throw new FormatException("invalid json");
            Parser parser = new Parser(value);
            parser.Value();
            parser.White();
            if (!parser.End) throw new FormatException("trailing json");
        }

        private sealed class Parser
        {
            private readonly string text;
            private int index;
            internal Parser(string value) { text = value; }
            internal bool End { get { return index == text.Length; } }
            internal void White()
            {
                while (!End && (text[index] == ' ' || text[index] == '\t' ||
                    text[index] == '\r' || text[index] == '\n')) index++;
            }
            private char Take() { if (End) throw new FormatException("json eof"); return text[index++]; }
            private bool Consume(char value) { if (!End && text[index] == value) { index++; return true; } return false; }

            internal void Value()
            {
                White();
                if (End) throw new FormatException("json value");
                char value = text[index];
                if (value == '{') Object();
                else if (value == '[') Array();
                else if (value == '"') String();
                else if (value == 't') Literal("true");
                else if (value == 'f') Literal("false");
                else if (value == 'n') Literal("null");
                else Number();
            }

            private void Object()
            {
                Take();
                White();
                if (Consume('}')) return;
                HashSet<string> keys = new HashSet<string>(StringComparer.Ordinal);
                while (true)
                {
                    White();
                    if (End || text[index] != '"') throw new FormatException("object key");
                    string key = String();
                    if (!keys.Add(key)) throw new FormatException("duplicate key");
                    White();
                    if (!Consume(':')) throw new FormatException("object colon");
                    Value();
                    White();
                    if (Consume('}')) return;
                    if (!Consume(',')) throw new FormatException("object comma");
                }
            }

            private void Array()
            {
                Take();
                White();
                if (Consume(']')) return;
                while (true)
                {
                    Value();
                    White();
                    if (Consume(']')) return;
                    if (!Consume(',')) throw new FormatException("array comma");
                }
            }

            private string String()
            {
                if (Take() != '"') throw new FormatException("string");
                System.Text.StringBuilder result = new System.Text.StringBuilder();
                while (true)
                {
                    char value = Take();
                    if (value == '"') return result.ToString();
                    if (value < 0x20) throw new FormatException("control");
                    if (value != '\\') { result.Append(value); continue; }
                    char escape = Take();
                    if (escape == '"' || escape == '\\' || escape == '/') result.Append(escape);
                    else if (escape == 'b') result.Append('\b');
                    else if (escape == 'f') result.Append('\f');
                    else if (escape == 'n') result.Append('\n');
                    else if (escape == 'r') result.Append('\r');
                    else if (escape == 't') result.Append('\t');
                    else if (escape == 'u') result.Append((char)Hex4());
                    else throw new FormatException("escape");
                }
            }

            private int Hex4()
            {
                int result = 0;
                for (int count = 0; count < 4; count++)
                {
                    char value = Take();
                    int digit = value >= '0' && value <= '9' ? value - '0' :
                        value >= 'a' && value <= 'f' ? value - 'a' + 10 :
                        value >= 'A' && value <= 'F' ? value - 'A' + 10 : -1;
                    if (digit < 0) throw new FormatException("unicode");
                    result = result * 16 + digit;
                }
                return result;
            }

            private void Literal(string value)
            {
                foreach (char expected in value)
                    if (Take() != expected) throw new FormatException("literal");
            }

            private void Number()
            {
                Consume('-');
                if (Consume('0')) { }
                else
                {
                    if (End || text[index] < '1' || text[index] > '9') throw new FormatException("number");
                    while (!End && text[index] >= '0' && text[index] <= '9') index++;
                }
                if (Consume('.'))
                {
                    if (End || text[index] < '0' || text[index] > '9') throw new FormatException("fraction");
                    while (!End && text[index] >= '0' && text[index] <= '9') index++;
                }
                if (!End && (text[index] == 'e' || text[index] == 'E'))
                {
                    index++;
                    if (!End && (text[index] == '+' || text[index] == '-')) index++;
                    if (End || text[index] < '0' || text[index] > '9') throw new FormatException("exponent");
                    while (!End && text[index] >= '0' && text[index] <= '9') index++;
                }
            }
        }
    }
}
'@
}

function Write-Result([string]$Code, [string]$Tag = $script:ResultTag) {
    [Console]::Out.WriteLine("target=$($script:Target) tag=$Tag result=$Code")
}

function Test-DecimalInt64([string]$Value, [ref]$Parsed) {
    if ($Value -notmatch '^[1-9][0-9]*$') { return $false }
    $number = 0L
    if (-not [Int64]::TryParse($Value, [Globalization.NumberStyles]::None,
            [Globalization.CultureInfo]::InvariantCulture, [ref]$number)) { return $false }
    if ($number -le 0) { return $false }
    $Parsed.Value = $number
    return $true
}

function Assert-LauncherContract {
    if (-not $ManagedPreflight) { throw "CONTRACT" }
    $guid = [Guid]::Empty
    if (-not [Guid]::TryParseExact($TransactionId, "D", [ref]$guid) -or
        $TransactionId -cne $guid.ToString("D")) { throw "CONTRACT" }
    foreach ($item in @(
        @{ Text = $StartTick; Name = "Start" },
        @{ Text = $MutationCutoffTick; Name = "Mutation" },
        @{ Text = $KillTick; Name = "Kill" },
        @{ Text = $HardDeadlineTick; Name = "Deadline" },
        @{ Text = $StopwatchFrequency; Name = "Frequency" }
    )) {
        $parsed = 0L
        if (-not (Test-DecimalInt64 $item.Text ([ref]$parsed))) { throw "CONTRACT" }
        Set-Variable -Scope Script -Name $item.Name -Value $parsed
    }
    if ($script:Frequency -ne [Diagnostics.Stopwatch]::Frequency) { throw "CONTRACT" }
    $startDecimal = [decimal]$script:Start
    $frequencyDecimal = [decimal]$script:Frequency
    if ([decimal]$script:Mutation -ne $startDecimal + 22 * $frequencyDecimal -or
        [decimal]$script:Kill -ne $startDecimal + 25 * $frequencyDecimal -or
        [decimal]$script:Deadline -ne $startDecimal + 30 * $frequencyDecimal) { throw "CONTRACT" }
    if ([Diagnostics.Stopwatch]::GetTimestamp() -ge $script:Deadline) { throw "CONTRACT" }
}

function Get-Sha256Bytes([byte[]]$Bytes) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace("-", "").ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Get-Sha256File([string]$Path) {
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace("-", "").ToLowerInvariant() }
    finally { $sha.Dispose(); $stream.Dispose() }
}

function Get-Utf8Text([byte[]]$Bytes) {
    $utf8 = New-Object Text.UTF8Encoding($false, $true)
    return $utf8.GetString($Bytes)
}

function Read-StrictJsonBytes([byte[]]$Bytes) {
    $text = Get-Utf8Text $Bytes
    [Foundation.StrictJson]::Validate($text)
    $command = Get-Command ConvertFrom-Json
    if ($command.Parameters.ContainsKey("DateKind")) { return $text | ConvertFrom-Json -DateKind String }
    return $text | ConvertFrom-Json
}

function Read-StrictJsonFile([string]$Path) {
    return Read-StrictJsonBytes ([IO.File]::ReadAllBytes($Path))
}

function Assert-ExactKeys($Object, [string[]]$Keys) {
    if ($null -eq $Object -or $Object -is [Array] -or $Object -is [string]) { throw "SCHEMA" }
    $actual = @($Object.PSObject.Properties | ForEach-Object { $_.Name })
    if ($actual.Count -ne $Keys.Count) { throw "SCHEMA" }
    foreach ($key in $Keys) {
        if ([Array]::IndexOf([string[]]$actual, $key) -lt 0) { throw "SCHEMA" }
    }
}

function Test-Integer($Value) {
    return $Value -is [int] -or $Value -is [long]
}

function Test-Sha256($Value) {
    return $Value -is [string] -and $Value -cmatch '^[0-9a-f]{64}$'
}

function Test-SemVer($Value) {
    return $Value -is [string] -and $Value -cmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
}

function Test-SafeRelativePath($Value) {
    if ($Value -isnot [string] -or -not $Value -or $Value.Contains("\") -or $Value.Contains(":")) { return $false }
    if ($Value.StartsWith("/") -or $Value.EndsWith("/") -or $Value.Contains("//")) { return $false }
    foreach ($part in $Value.Split('/')) { if ($part -eq "." -or $part -eq ".." -or -not $part) { return $false } }
    return [IO.Path]::GetExtension($Value).ToLowerInvariant() -in @(".json", ".md", ".toml", ".txt", ".yaml", ".yml")
}

function Test-ReparseAtOrAbove([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if ([IO.File]::Exists($full)) { $current = New-Object IO.FileInfo($full) }
    elseif ([IO.Directory]::Exists($full)) { $current = New-Object IO.DirectoryInfo($full) }
    else { $current = New-Object IO.DirectoryInfo([IO.Path]::GetDirectoryName($full)) }
    while ($null -ne $current) {
        if ($current.Exists -and (($current.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { return $true }
        if ($current -is [IO.FileInfo]) { $current = $current.Directory } else { $current = $current.Parent }
    }
    return $false
}

function Test-ReparseTree([string]$Path) {
    if (Test-ReparseAtOrAbove $Path) { return $true }
    if (-not [IO.Directory]::Exists($Path)) { return $false }
    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    $pending.Push([IO.Path]::GetFullPath($Path))
    while ($pending.Count -gt 0) {
        $current = $pending.Pop()
        foreach ($entry in (New-Object IO.DirectoryInfo($current)).EnumerateFileSystemInfos()) {
            if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return $true }
            if ($entry -is [IO.DirectoryInfo]) { $pending.Push($entry.FullName) }
        }
    }
    return $false
}

function Get-Fingerprint([string]$Path) {
    if (-not [IO.File]::Exists($Path) -and -not [IO.Directory]::Exists($Path)) { return "absent" }
    if (Test-ReparseTree $Path) { throw "REPARSE" }
    if ([IO.File]::Exists($Path)) { return Get-Sha256File $Path }
    $root = [IO.Path]::GetFullPath($Path).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $relative = New-Object 'System.Collections.Generic.List[string]'
    foreach ($file in [IO.Directory]::GetFiles($root, "*", [IO.SearchOption]::AllDirectories)) {
        if (Test-ReparseAtOrAbove $file) { throw "REPARSE" }
        $relative.Add($file.Substring($root.Length + 1).Replace("\", "/"))
    }
    $items = $relative.ToArray()
    [Array]::Sort($items, [StringComparer]::Ordinal)
    $builder = New-Object Text.StringBuilder
    foreach ($name in $items) {
        $filePath = [IO.Path]::Combine($root, $name.Replace('/', [IO.Path]::DirectorySeparatorChar))
        [void]$builder.Append($name).Append([char]0).Append((Get-Sha256File $filePath)).Append("`n")
    }
    return Get-Sha256Bytes ((New-Object Text.UTF8Encoding($false)).GetBytes($builder.ToString()))
}

function ConvertTo-CanonicalJsonBytes($Value) {
    $text = ($Value | ConvertTo-Json -Depth 20 -Compress) + "`n"
    return (New-Object Text.UTF8Encoding($false)).GetBytes($text)
}

function Write-DurableBytes([string]$Path, [byte[]]$Bytes) {
    $parent = [IO.Path]::GetDirectoryName($Path)
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    $temp = [IO.Path]::Combine($parent, "." + [IO.Path]::GetFileName($Path) + "." + [Guid]::NewGuid().ToString("N") + ".tmp")
    $stream = New-Object IO.FileStream($temp, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
        [IO.FileShare]::None, 4096, [IO.FileOptions]::WriteThrough)
    try { $stream.Write($Bytes, 0, $Bytes.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
    try {
        if ([IO.File]::Exists($Path)) {
            $backup = $temp + ".bak"
            [IO.File]::Replace($temp, $Path, $backup)
            if ([IO.File]::Exists($backup)) { [IO.File]::Delete($backup) }
        }
        else { [IO.File]::Move($temp, $Path) }
    }
    finally { if ([IO.File]::Exists($temp)) { [IO.File]::Delete($temp) } }
}

function Assert-Before([long]$Tick, [string]$Code) {
    if ([Diagnostics.Stopwatch]::GetTimestamp() -ge $Tick) { throw $Code }
}

function Acquire-TargetLock([string]$Path) {
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Path)) | Out-Null
    while ([Diagnostics.Stopwatch]::GetTimestamp() -lt $script:Mutation) {
        try {
            $stream = New-Object IO.FileStream($Path, [IO.FileMode]::OpenOrCreate,
                [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
            $stream.Lock(0, 1)
            $script:LockStream = $stream
            return $true
        }
        catch [IO.IOException] {
            if ($null -ne $stream) { $stream.Dispose() }
            Start-Sleep -Milliseconds 25
        }
    }
    return $false
}

function ConvertTo-WindowsArgument([string]$Value) {
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    $builder = New-Object Text.StringBuilder
    [void]$builder.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') { $slashes++; continue }
        if ($character -eq '"') {
            [void]$builder.Append(('\' * (2 * $slashes + 1))).Append('"')
            $slashes = 0
            continue
        }
        if ($slashes -gt 0) { [void]$builder.Append(('\' * $slashes)); $slashes = 0 }
        [void]$builder.Append($character)
    }
    if ($slashes -gt 0) { [void]$builder.Append(('\' * (2 * $slashes))) }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Invoke-Gh([string[]]$Arguments) {
    Assert-Before $script:Mutation "PREMUTATION_TIMEOUT"
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $script:GhPath
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = New-Object Text.UTF8Encoding($false, $true)
    $info.Arguments = (($Arguments | ForEach-Object { ConvertTo-WindowsArgument $_ }) -join " ")
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        if (-not $process.Start()) { throw "GH_REQUIRED" }
    }
    catch { throw "GH_REQUIRED" }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $remainingTicks = $script:Mutation - [Diagnostics.Stopwatch]::GetTimestamp()
    $milliseconds = [Math]::Max(1, [Math]::Min([Int32]::MaxValue,
        [long]($remainingTicks * 1000L / $script:Frequency)))
    if (-not $process.WaitForExit([int]$milliseconds)) {
        try { $process.Kill() } catch { }
        try { $process.WaitForExit() } catch { }
        throw "PREMUTATION_TIMEOUT"
    }
    try {
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
    }
    catch { throw "GH_COMMAND_FAILED" }
    if ($process.ExitCode -ne 0) { throw "GH_COMMAND_FAILED" }
    return $stdout
}

function Assert-GhJson([string[]]$Arguments) {
    $output = Invoke-Gh $Arguments
    $bytes = (New-Object Text.UTF8Encoding($false, $true)).GetBytes($output)
    $null = Read-StrictJsonBytes $bytes
}

function Get-LatestStableRelease {
    $projection = '[.[] | select((.draft == false) and (.prerelease == false)) | {tag_name, draft, prerelease, published_at}]'
    try {
        $output = Invoke-Gh @("api", "repos/$($script:Repository)/releases?per_page=20", "--jq", $projection)
    }
    catch {
        if ([string]$_.Exception.Message -eq "GH_COMMAND_FAILED") { throw "NETWORK_UNAVAILABLE" }
        throw
    }
    if (-not $output.TrimStart().StartsWith("[", [StringComparison]::Ordinal)) { throw "RELEASE_SCHEMA" }
    $parsedReleases = Read-StrictJsonBytes ((New-Object Text.UTF8Encoding($false, $true)).GetBytes($output))
    $releases = @($parsedReleases)
    $best = $null
    foreach ($release in $releases) {
        Assert-ExactKeys $release @("tag_name", "draft", "prerelease", "published_at")
        if ($release.tag_name -isnot [string] -or $release.draft -isnot [bool] -or
            $release.prerelease -isnot [bool] -or
            $release.published_at -isnot [string]) { throw "RELEASE_SCHEMA" }
        if ($release.draft -or $release.prerelease) { continue }
        if ($release.tag_name -cnotmatch '^opencode-v((0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*))$') { continue }
        $candidateVersion = [version]$Matches[1]
        if ($null -eq $best -or $candidateVersion -gt $best.Version) {
            $best = [pscustomobject]@{ Record = $release; Version = $candidateVersion; Text = $Matches[1] }
        }
    }
    if ($null -eq $best) { throw "NO_STABLE_RELEASE" }
    return $best
}

function Assert-ReleaseManifest($Manifest, [string]$Tag, [string]$Version) {
    Assert-ExactKeys $Manifest @(
        "schema_version", "target", "version", "tag", "channel", "client",
        "foundation_engine_version", "foundation_engine_manifest_sha256", "source", "asset",
        "session_tools_asset", "package_manifest_sha256", "components_lock_sha256", "requires",
        "acceptance_evidence_sha256", "promoted_from_candidate_manifest_sha256"
    )
    if (-not (Test-Integer $Manifest.schema_version) -or $Manifest.schema_version -ne 1 -or
        $Manifest.target -cne "opencode" -or $Manifest.version -cne $Version -or
        $Manifest.tag -cne $Tag -or $Manifest.channel -cne "stable" -or
        -not (Test-SemVer $Manifest.foundation_engine_version) -or
        -not (Test-Sha256 $Manifest.foundation_engine_manifest_sha256) -or
        -not (Test-Sha256 $Manifest.package_manifest_sha256) -or
        -not (Test-Sha256 $Manifest.components_lock_sha256) -or
        -not (Test-Sha256 $Manifest.acceptance_evidence_sha256) -or
        -not (Test-Sha256 $Manifest.promoted_from_candidate_manifest_sha256)) { throw "RELEASE_MANIFEST" }
    Assert-ExactKeys $Manifest.client @("id", "supported_version")
    Assert-ExactKeys $Manifest.source @("repository", "commit", "tree", "transformation")
    Assert-ExactKeys $Manifest.asset @("name", "sha256", "bytes")
    Assert-ExactKeys $Manifest.session_tools_asset @("name", "sha256", "bytes", "manifest_sha256", "tool_count", "file_count")
    Assert-ExactKeys $Manifest.requires @("immutable_release", "release_attestation", "verification_commands")
    if ($Manifest.client.id -cne "opencode" -or $Manifest.source.repository -cne "https://github.com/$($script:Repository)" -or
        $Manifest.client.supported_version -isnot [string] -or -not $Manifest.client.supported_version -or
        $Manifest.source.commit -cnotmatch '^[0-9a-f]{40}$' -or $Manifest.source.tree -cnotmatch '^[0-9a-f]{40}$' -or
        $Manifest.source.transformation -cne "opencode-native-v1" -or
        $Manifest.asset.name -cne "opencode-base-$Version.zip" -or
        -not (Test-Sha256 $Manifest.asset.sha256) -or -not (Test-Integer $Manifest.asset.bytes) -or $Manifest.asset.bytes -le 0 -or
        -not (Test-Sha256 $Manifest.session_tools_asset.sha256) -or
        -not (Test-Sha256 $Manifest.session_tools_asset.manifest_sha256) -or
        -not (Test-Integer $Manifest.session_tools_asset.bytes) -or $Manifest.session_tools_asset.bytes -le 0 -or
        $Manifest.session_tools_asset.bytes -gt 10485760 -or
        -not (Test-Integer $Manifest.session_tools_asset.tool_count) -or
        -not (Test-Integer $Manifest.session_tools_asset.file_count) -or
        $Manifest.session_tools_asset.file_count -lt 1 -or $Manifest.session_tools_asset.file_count -gt 256 -or
        $Manifest.requires.immutable_release -isnot [bool] -or -not $Manifest.requires.immutable_release -or
        $Manifest.requires.release_attestation -isnot [bool] -or -not $Manifest.requires.release_attestation -or
        $Manifest.requires.verification_commands -isnot [Array] -or
        $Manifest.requires.verification_commands.Count -ne 2 -or
        $Manifest.requires.verification_commands[0] -cne "gh release verify $Tag -R $($script:Repository)" -or
        $Manifest.requires.verification_commands[1] -cne "gh release verify-asset $Tag opencode-base-$Version.zip -R $($script:Repository)") {
        throw "RELEASE_MANIFEST"
    }
    if ($Manifest.session_tools_asset.tool_count -ne 1) { throw "BLOCKED_MULTI_TOOL_ASSET" }
    $expectedName = "session-tools-opencode-$Version.zip"
    if ($Manifest.session_tools_asset.name -cne $expectedName) { throw "RELEASE_MANIFEST" }
}

function Read-SessionArchive([string]$Path, [string]$ExpectedManifestHash, [string]$Tag, [string]$Version) {
    if ((Get-Item -LiteralPath $Path).Length -gt 10485760) { throw "SESSION_ARCHIVE" }
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None)
    $archive = New-Object IO.Compression.ZipArchive($stream, [IO.Compression.ZipArchiveMode]::Read, $false)
    try {
        $entries = @($archive.Entries)
        if ($entries.Count -lt 2 -or $entries.Count -gt 257) { throw "SESSION_ARCHIVE" }
        $names = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
        $folded = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
        $payloads = @{}
        $expanded = 0L
        foreach ($entry in $entries) {
            $name = $entry.FullName
            if (-not $name -or $name.EndsWith("/") -or -not $names.Add($name) -or -not $folded.Add($name) -or
                $name.Contains("\") -or $name.Contains(":") -or $name.StartsWith("/") -or $name.Contains("//")) { throw "SESSION_ARCHIVE" }
            foreach ($part in $name.Split('/')) { if (-not $part -or $part -eq "." -or $part -eq "..") { throw "SESSION_ARCHIVE" } }
            $unixMode = ([int64]$entry.ExternalAttributes -shr 16) -band 0xffff
            if (($unixMode -band 0xF000) -eq 0xA000 -or ($unixMode -band 0x49) -ne 0) { throw "SESSION_ARCHIVE" }
            if ($entry.Length -gt 1048576) { throw "SESSION_ARCHIVE" }
            $expanded += $entry.Length
            if ($expanded -gt 8388608) { throw "SESSION_ARCHIVE" }
            $entryStream = $entry.Open()
            $memory = New-Object IO.MemoryStream
            try { $entryStream.CopyTo($memory); $bytes = $memory.ToArray() }
            finally { $memory.Dispose(); $entryStream.Dispose() }
            if ($bytes.Length -ne $entry.Length) { throw "SESSION_ARCHIVE" }
            $payloads[$name] = $bytes
        }
        if (-not $payloads.ContainsKey("session-tools-manifest.json")) { throw "SESSION_ARCHIVE" }
        $manifestBytes = [byte[]]$payloads["session-tools-manifest.json"]
        if ((Get-Sha256Bytes $manifestBytes) -cne $ExpectedManifestHash) { throw "SESSION_ARCHIVE" }
        $manifest = Read-StrictJsonBytes $manifestBytes
        Assert-ExactKeys $manifest @("schema_version", "target", "release_tag", "base_version", "tools")
        if (-not (Test-Integer $manifest.schema_version) -or $manifest.schema_version -ne 1 -or
            $manifest.target -cne "opencode" -or $manifest.release_tag -cne $Tag -or
            $manifest.base_version -cne $Version -or $manifest.tools -isnot [Array]) { throw "SESSION_MANIFEST" }
        if ($manifest.tools.Count -ne 1) { throw "BLOCKED_MULTI_TOOL_ASSET" }
        $tool = $manifest.tools[0]
        Assert-ExactKeys $tool @("id", "files")
        if ($tool.id -isnot [string] -or $tool.id -cnotmatch '^[a-z0-9][a-z0-9-]{0,63}$' -or
            $tool.files -isnot [Array] -or $tool.files.Count -lt 1 -or $tool.files.Count -gt 256) { throw "SESSION_MANIFEST" }
        $expectedNames = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
        [void]$expectedNames.Add("session-tools-manifest.json")
        $previousPath = ""
        $stateFiles = @()
        foreach ($file in $tool.files) {
            Assert-ExactKeys $file @("path", "sha256", "bytes")
            if (-not (Test-SafeRelativePath $file.path) -or $file.path -cle $previousPath -or
                -not (Test-Sha256 $file.sha256) -or -not (Test-Integer $file.bytes) -or
                $file.bytes -lt 0 -or $file.bytes -gt 1048576) { throw "SESSION_MANIFEST" }
            $previousPath = $file.path
            $entryName = "tools/$($tool.id)/$($file.path)"
            if (-not $expectedNames.Add($entryName) -or -not $payloads.ContainsKey($entryName)) { throw "SESSION_MANIFEST" }
            $bytes = [byte[]]$payloads[$entryName]
            if ($bytes.Length -ne $file.bytes -or (Get-Sha256Bytes $bytes) -cne $file.sha256) { throw "SESSION_MANIFEST" }
            $stateFiles += [ordered]@{ path = $file.path; sha256 = $file.sha256; bytes = [int64]$file.bytes }
        }
        if ($expectedNames.Count -ne $payloads.Count) { throw "SESSION_ARCHIVE" }
        return [pscustomobject]@{
            Manifest = $manifest
            ManifestBytes = $manifestBytes
            ToolId = $tool.id
            Files = $stateFiles
            Payloads = $payloads
        }
    }
    finally { $archive.Dispose(); $stream.Dispose() }
}

function Assert-OperationMap($Operations, [string]$Phase) {
    $names = @("move_destination_to_previous", "move_staging_to_destination", "write_state")
    Assert-ExactKeys $Operations $names
    $bits = @()
    foreach ($name in $names) {
        $record = $Operations.$name
        Assert-ExactKeys $record @("intent", "applied")
        if ($record.intent -isnot [bool] -or $record.applied -isnot [bool] -or ($record.applied -and -not $record.intent)) { throw "JOURNAL" }
        $bits += $record.intent; $bits += $record.applied
    }
    $phases = @("created", "staged", "move_destination_intent", "move_destination_applied", "move_staging_intent", "move_staging_applied", "state_write_intent", "state_write_applied", "committed")
    $phaseIndex = [Array]::IndexOf([string[]]$phases, $Phase)
    if ($phaseIndex -lt 0) { throw "JOURNAL" }
    $expected = @($false, $false, $false, $false, $false, $false)
    if ($phaseIndex -ge 2) {
        $transition = $phaseIndex - 2
        for ($index = 0; $index -le $transition -and $index -lt 6; $index++) { $expected[$index] = $true }
    }
    if ($Phase -eq "committed") { for ($index = 0; $index -lt 6; $index++) { $expected[$index] = $true } }
    for ($index = 0; $index -lt 6; $index++) { if ($bits[$index] -ne $expected[$index]) { throw "JOURNAL" } }
}

function Assert-Journal($Journal, [string]$JournalPath, [string]$ReceiptHash) {
    $keys = @("schema_version", "target", "transaction_id", "phase", "receipt_sha256",
        "start_tick", "mutation_cutoff_tick", "kill_tick", "hard_deadline_tick", "stopwatch_frequency",
        "previous_destination_sha256", "previous_state_sha256", "expected_staging_sha256",
        "expected_destination_sha256", "expected_state_sha256", "staging_path", "previous_path",
        "destination_path", "state_path", "operations")
    Assert-ExactKeys $Journal $keys
    $guid = [Guid]::Empty
    if (-not (Test-Integer $Journal.schema_version) -or $Journal.schema_version -ne 1 -or
        $Journal.target -cne "opencode" -or -not [Guid]::TryParseExact($Journal.transaction_id, "D", [ref]$guid) -or
        $Journal.transaction_id -cne $guid.ToString("D") -or $Journal.receipt_sha256 -cne $ReceiptHash) { throw "JOURNAL" }
    foreach ($key in @("start_tick", "mutation_cutoff_tick", "kill_tick", "hard_deadline_tick", "stopwatch_frequency")) {
        if (-not (Test-Integer $Journal.$key) -or $Journal.$key -le 0) { throw "JOURNAL" }
    }
    if ($Journal.stopwatch_frequency -ne $script:Frequency -or
        [decimal]$Journal.mutation_cutoff_tick -ne [decimal]$Journal.start_tick + 22 * [decimal]$script:Frequency -or
        [decimal]$Journal.kill_tick -ne [decimal]$Journal.start_tick + 25 * [decimal]$script:Frequency -or
        [decimal]$Journal.hard_deadline_tick -ne [decimal]$Journal.start_tick + 30 * [decimal]$script:Frequency -or
        $Journal.hard_deadline_tick -gt $script:Deadline) { throw "JOURNAL" }
    foreach ($key in @("previous_destination_sha256", "previous_state_sha256", "expected_staging_sha256", "expected_destination_sha256", "expected_state_sha256")) {
        if ($Journal.$key -cne "absent" -and -not (Test-Sha256 $Journal.$key)) { throw "JOURNAL" }
    }
    $stateRoot = [IO.Path]::GetDirectoryName($JournalPath)
    $transactionRoot = [IO.Path]::Combine($stateRoot, "transactions", $guid.ToString("D"))
    $skillsRoot = [IO.Path]::GetFullPath([IO.Path]::Combine($env:USERPROFILE, ".config", "opencode", "skills"))
    $expectedState = [IO.Path]::Combine($stateRoot, "state.json")
    if ([IO.Path]::GetFullPath($Journal.staging_path) -cne [IO.Path]::Combine($transactionRoot, "staging") -or
        [IO.Path]::GetFullPath($Journal.previous_path) -cne [IO.Path]::Combine($transactionRoot, "previous") -or
        [IO.Path]::GetFullPath($Journal.state_path) -cne $expectedState -or
        [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($Journal.destination_path)) -cne $skillsRoot) { throw "JOURNAL" }
    if ([IO.File]::Exists($Journal.staging_path)) { throw "JOURNAL" }
    foreach ($path in @($transactionRoot, $Journal.staging_path, $Journal.previous_path, $Journal.destination_path, $Journal.state_path)) {
        if (Test-ReparseTree $path) { throw "JOURNAL" }
    }
    Assert-OperationMap $Journal.operations $Journal.phase
}

function Remove-Entry([string]$Path) {
    if ([IO.File]::Exists($Path)) { throw "RECOVERY" }
    if (-not [IO.Directory]::Exists($Path)) { return }
    if ($env:LLM_FOUNDATION_TEST_PRE_DELETE_BARRIER -ceq "1") {
        $transactionRoot = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($Path))
        $stateRoot = [IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($transactionRoot))
        $barrierName = ".test-pre-delete." + [IO.Path]::GetFileName($transactionRoot)
        $ready = [IO.Path]::Combine($stateRoot, $barrierName + ".ready")
        $resume = [IO.Path]::Combine($stateRoot, $barrierName + ".continue")
        Write-DurableBytes $ready ([byte[]]@())
        try {
            while (-not [IO.File]::Exists($resume)) {
                Assert-Before $script:Deadline "RECOVERY_TIMEOUT"
                Start-Sleep -Milliseconds 10
            }
        }
        finally {
            if ([IO.File]::Exists($resume)) { [IO.File]::Delete($resume) }
            if ([IO.File]::Exists($ready)) { [IO.File]::Delete($ready) }
        }
    }
    if (Test-ReparseTree $Path) { throw "RECOVERY" }
    [IO.Directory]::Delete($Path, $true)
}

function Remove-EmptyTransactionRoot([string]$Path) {
    if ([IO.File]::Exists($Path)) { throw "RECOVERY" }
    if (-not [IO.Directory]::Exists($Path)) { return }
    if (Test-ReparseTree $Path) { throw "RECOVERY" }
    if ([IO.Directory]::GetFileSystemEntries($Path).Count -ne 0) { throw "RECOVERY" }
    [IO.Directory]::Delete($Path, $false)
}

function Recover-ActiveTransaction([string]$JournalPath, [string]$ReceiptHash) {
    if (-not [IO.File]::Exists($JournalPath)) { return $true }
    $journal = Read-StrictJsonFile $JournalPath
    Assert-Journal $journal $JournalPath $ReceiptHash
    Assert-Before $script:Deadline "RECOVERY_TIMEOUT"
    $destinationHash = Get-Fingerprint $journal.destination_path
    $previousHash = Get-Fingerprint $journal.previous_path
    $stagingHash = Get-Fingerprint $journal.staging_path
    $stateHash = Get-Fingerprint $journal.state_path

    if ($journal.phase -ceq "created" -and
        $destinationHash -ceq $journal.previous_destination_sha256 -and
        $previousHash -ceq "absent" -and
        $stateHash -ceq $journal.previous_state_sha256) {
        Remove-Entry $journal.staging_path
    }
    elseif ($destinationHash -ceq $journal.expected_destination_sha256 -and $stateHash -ceq $journal.expected_state_sha256) {
        if ($previousHash -cne "absent" -and $previousHash -cne $journal.previous_destination_sha256) { throw "RECOVERY" }
        if ($stagingHash -cne "absent" -and $stagingHash -cne $journal.expected_staging_sha256) { throw "RECOVERY" }
        Remove-Entry $journal.previous_path
        Remove-Entry $journal.staging_path
    }
    elseif ($stateHash -ceq $journal.previous_state_sha256) {
        if ($destinationHash -ceq $journal.expected_destination_sha256) {
            Remove-Entry $journal.destination_path
            $destinationHash = "absent"
        }
        if ($stagingHash -ceq $journal.expected_staging_sha256) { Remove-Entry $journal.staging_path; $stagingHash = "absent" }
        if ($stagingHash -cne "absent") { throw "RECOVERY" }
        if ($journal.previous_destination_sha256 -ceq "absent") {
            if ($previousHash -cne "absent" -or $destinationHash -cne "absent") { throw "RECOVERY" }
        }
        elseif ($destinationHash -ceq $journal.previous_destination_sha256 -and $previousHash -ceq "absent") { }
        elseif ($destinationHash -ceq "absent" -and $previousHash -ceq $journal.previous_destination_sha256) {
            [IO.Directory]::Move($journal.previous_path, $journal.destination_path)
        }
        else { throw "RECOVERY" }
    }
    else { throw "RECOVERY" }

    $expectedDestination = if ($stateHash -ceq $journal.expected_state_sha256) { $journal.expected_destination_sha256 } else { $journal.previous_destination_sha256 }
    if ((Get-Fingerprint $journal.destination_path) -cne $expectedDestination -or
        (Get-Fingerprint $journal.state_path) -cne $stateHash -or
        (Get-Fingerprint $journal.previous_path) -cne "absent" -or
        (Get-Fingerprint $journal.staging_path) -cne "absent") { throw "RECOVERY" }
    $transactionRoot = [IO.Path]::GetDirectoryName($journal.staging_path)
    Remove-EmptyTransactionRoot $transactionRoot
    [IO.File]::Delete($JournalPath)
    return $true
}

function Set-JournalPhase($Journal, [string]$Phase) {
    $Journal.phase = $Phase
    switch ($Phase) {
        "move_destination_intent" { $Journal.operations.move_destination_to_previous.intent = $true }
        "move_destination_applied" { $Journal.operations.move_destination_to_previous.intent = $true; $Journal.operations.move_destination_to_previous.applied = $true }
        "move_staging_intent" { $Journal.operations.move_destination_to_previous.intent = $true; $Journal.operations.move_destination_to_previous.applied = $true; $Journal.operations.move_staging_to_destination.intent = $true }
        "move_staging_applied" { $Journal.operations.move_destination_to_previous.intent = $true; $Journal.operations.move_destination_to_previous.applied = $true; $Journal.operations.move_staging_to_destination.intent = $true; $Journal.operations.move_staging_to_destination.applied = $true }
        "state_write_intent" { $Journal.operations.move_destination_to_previous.intent = $true; $Journal.operations.move_destination_to_previous.applied = $true; $Journal.operations.move_staging_to_destination.intent = $true; $Journal.operations.move_staging_to_destination.applied = $true; $Journal.operations.write_state.intent = $true }
        { $_ -in @("state_write_applied", "committed") } { $Journal.operations.move_destination_to_previous.intent = $true; $Journal.operations.move_destination_to_previous.applied = $true; $Journal.operations.move_staging_to_destination.intent = $true; $Journal.operations.move_staging_to_destination.applied = $true; $Journal.operations.write_state.intent = $true; $Journal.operations.write_state.applied = $true }
    }
    Write-DurableBytes $script:JournalPath (ConvertTo-CanonicalJsonBytes $Journal)
    if ($env:LLM_FOUNDATION_TEST_STOP_AFTER_PHASE -ceq $Phase) { [Diagnostics.Process]::GetCurrentProcess().Kill() }
}

function Get-ToolRecordFingerprint($Tool, [string]$ExpectedId) {
    Assert-ExactKeys $Tool @("id", "files")
    if ($Tool.id -cne $ExpectedId -or $Tool.files -isnot [Array] -or
        $Tool.files.Count -lt 1 -or $Tool.files.Count -gt 256) { throw "TOOL_RECORD" }
    $builder = New-Object Text.StringBuilder
    $previousPath = ""
    foreach ($file in $Tool.files) {
        Assert-ExactKeys $file @("path", "sha256", "bytes")
        if (-not (Test-SafeRelativePath $file.path) -or $file.path -cle $previousPath -or
            -not (Test-Sha256 $file.sha256) -or -not (Test-Integer $file.bytes) -or
            $file.bytes -lt 0 -or $file.bytes -gt 1048576) { throw "TOOL_RECORD" }
        $previousPath = $file.path
        [void]$builder.Append($file.path).Append([char]0).Append($file.sha256).Append("`n")
    }
    return Get-Sha256Bytes ((New-Object Text.UTF8Encoding($false)).GetBytes($builder.ToString()))
}

function Assert-OwnedDestination([string]$Destination, [string]$StatePath, [string]$BaselinePath,
    [string]$ToolId, [string]$InstalledBaseVersion) {
    $actual = Get-Fingerprint $Destination
    if ($actual -ceq "absent") {
        if ([IO.File]::Exists($StatePath)) { throw "BLOCKED_MANAGED_DRIFT" }
        return
    }
    if ([IO.File]::Exists($StatePath)) {
        if (Test-ReparseAtOrAbove $StatePath) { throw "BLOCKED_MANAGED_DRIFT" }
        $state = Read-StrictJsonFile $StatePath
        Assert-ExactKeys $state @("schema_version", "target", "release_tag", "release_version", "release_manifest_sha256", "session_manifest_sha256", "verified_at", "tools")
        if (-not (Test-Integer $state.schema_version) -or $state.schema_version -ne 1 -or $state.target -cne "opencode" -or
            -not (Test-SemVer $state.release_version) -or $state.release_tag -cne "opencode-v$($state.release_version)" -or
            -not (Test-Sha256 $state.release_manifest_sha256) -or -not (Test-Sha256 $state.session_manifest_sha256) -or
            $state.verified_at -isnot [string] -or $state.verified_at -cnotmatch '^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z$' -or
            $state.tools -isnot [Array] -or $state.tools.Count -ne 1) { throw "BLOCKED_MANAGED_DRIFT" }
        $tool = $state.tools[0]
        Assert-ExactKeys $tool @("id", "destination", "ownership_marker", "files")
        if ($tool.id -cne $ToolId -or [IO.Path]::GetFullPath($tool.destination) -cne [IO.Path]::GetFullPath($Destination) -or
            $tool.ownership_marker -cne "session-tools-v1:opencode:$ToolId") { throw "BLOCKED_MANAGED_DRIFT" }
        try { $expected = Get-ToolRecordFingerprint ([pscustomobject]@{ id = $tool.id; files = $tool.files }) $ToolId }
        catch { throw "BLOCKED_MANAGED_DRIFT" }
        if ($actual -cne $expected) { throw "BLOCKED_MANAGED_DRIFT" }
        return
    }
    if (-not [IO.File]::Exists($BaselinePath) -or (Test-ReparseAtOrAbove $BaselinePath)) { throw "BLOCKED_UNMANAGED_COLLISION" }
    $baseline = Read-StrictJsonFile $BaselinePath
    Assert-ExactKeys $baseline @("schema_version", "target", "release_tag", "base_version", "tools")
    if (-not (Test-Integer $baseline.schema_version) -or $baseline.schema_version -ne 1 -or
        $baseline.target -cne "opencode" -or -not (Test-SemVer $baseline.base_version) -or
        $baseline.base_version -cne $InstalledBaseVersion -or
        $baseline.release_tag -cne "opencode-v$($baseline.base_version)" -or $baseline.tools -isnot [Array]) {
        throw "BLOCKED_UNMANAGED_COLLISION"
    }
    $baselineTool = @($baseline.tools | Where-Object { $_.id -ceq $ToolId })
    if ($baselineTool.Count -ne 1) { throw "BLOCKED_UNMANAGED_COLLISION" }
    try { $expected = Get-ToolRecordFingerprint $baselineTool[0] $ToolId }
    catch { throw "BLOCKED_UNMANAGED_COLLISION" }
    if ($actual -cne $expected) { throw "BLOCKED_UNMANAGED_COLLISION" }
}

function Get-CurrentManagedReleaseVersion([string]$Profile, [string]$StatePath) {
    if (-not [IO.File]::Exists($StatePath)) { return $null }
    try {
        $state = Read-StrictJsonFile $StatePath
        if ($state.tools -isnot [Array] -or $state.tools.Count -ne 1 -or
            $state.tools[0].id -isnot [string] -or
            $state.tools[0].id -cnotmatch '^[a-z0-9][a-z0-9-]{0,63}$') {
            throw "BLOCKED_MANAGED_DRIFT"
        }
        $toolId = [string]$state.tools[0].id
        $destination = [IO.Path]::Combine($Profile, ".config", "opencode", "skills", $toolId)
        Assert-OwnedDestination $destination $StatePath "" $toolId ""
        return [string]$state.release_version
    }
    catch { throw "BLOCKED_MANAGED_DRIFT" }
}

function Invoke-Update {
    Assert-LauncherContract
    if (-not $env:USERPROFILE -or -not [IO.Path]::IsPathRooted($env:USERPROFILE)) { throw "CONTRACT" }
    $profile = [IO.Path]::GetFullPath($env:USERPROFILE)
    $stateRoot = [IO.Path]::Combine($profile, ".llm-foundation", "state", "session-tools", "opencode")
    $script:JournalPath = [IO.Path]::Combine($stateRoot, "active-transaction.json")
    $statePath = [IO.Path]::Combine($stateRoot, "state.json")
    $receiptPath = [IO.Path]::Combine($profile, ".llm-foundation", "bin", "opencode-managed.receipt.json")
    if (-not [IO.File]::Exists($receiptPath) -or (Test-ReparseAtOrAbove $receiptPath)) { throw "CONTRACT" }
    $receiptHash = Get-Sha256File $receiptPath
    $lockAcquired = Acquire-TargetLock ([IO.Path]::Combine($stateRoot, "update.lock"))
    if (-not $lockAcquired) { Write-Result "SKIPPED_LOCK_BUSY"; return }
    try {
        if ([IO.File]::Exists($script:JournalPath)) {
            try {
                if (-not (Recover-ActiveTransaction $script:JournalPath $receiptHash)) { throw "JOURNAL" }
            }
            catch { throw "JOURNAL" }
        }

        $command = Get-Command gh.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -eq $command -or -not [IO.File]::Exists($command.Path)) { throw "GH_REQUIRED" }
        $script:GhPath = [IO.Path]::GetFullPath($command.Path)
        if (Test-ReparseAtOrAbove $script:GhPath) { throw "GH_REQUIRED" }
        $latest = Get-LatestStableRelease
        $tag = $latest.Record.tag_name
        $version = $latest.Text
        $script:ResultTag = $tag

        $currentManagedVersion = Get-CurrentManagedReleaseVersion $profile $statePath
        if ($null -ne $currentManagedVersion -and [version]$currentManagedVersion -ge [version]$version) {
            Write-Result "NO_UPDATE" $tag
            return
        }

        $script:TempRoot = [IO.Path]::Combine([IO.Path]::GetTempPath(), "opencode-session-tools-" + [Guid]::NewGuid().ToString("N"))
        [IO.Directory]::CreateDirectory($script:TempRoot) | Out-Null
        $null = Invoke-Gh @("release", "download", $tag, "-R", $script:Repository, "-p", "release-manifest.json", "-D", $script:TempRoot, "--clobber")
        $releaseManifestPath = [IO.Path]::Combine($script:TempRoot, "release-manifest.json")
        if (-not [IO.File]::Exists($releaseManifestPath)) { throw "RELEASE_MANIFEST" }
        $releaseManifestBytes = [IO.File]::ReadAllBytes($releaseManifestPath)
        $releaseManifest = Read-StrictJsonBytes $releaseManifestBytes
        Assert-ReleaseManifest $releaseManifest $tag $version

        $asset = $releaseManifest.session_tools_asset
        $null = Invoke-Gh @("release", "download", $tag, "-R", $script:Repository, "-p", $asset.name, "-D", $script:TempRoot, "--clobber")
        $assetPath = [IO.Path]::Combine($script:TempRoot, $asset.name)
        if (-not [IO.File]::Exists($assetPath) -or (Get-Item -LiteralPath $assetPath).Length -ne $asset.bytes -or
            (Get-Sha256File $assetPath) -cne $asset.sha256) { throw "SESSION_ASSET" }
        $session = Read-SessionArchive $assetPath $asset.manifest_sha256 $tag $version
        if ($session.Files.Count -ne $asset.file_count) { throw "SESSION_ASSET" }

        $skillsRoot = [IO.Path]::Combine($profile, ".config", "opencode", "skills")
        $destination = [IO.Path]::Combine($skillsRoot, $session.ToolId)
        $baseRoot = [IO.Path]::Combine($profile, ".config", "opencode", "base")
        $baselinePath = [IO.Path]::Combine($baseRoot, "runtime", "session-tools-baseline.json")
        $baseVersionPath = [IO.Path]::Combine($baseRoot, "VERSION")
        if (-not [IO.File]::Exists($baseVersionPath) -or (Test-ReparseAtOrAbove $baseVersionPath)) { throw "BASE_VERSION" }
        $baseVersionText = Get-Utf8Text ([IO.File]::ReadAllBytes($baseVersionPath))
        if ($baseVersionText -cnotmatch '^((0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*))\r?\n?$') { throw "BASE_VERSION" }
        $installedBaseVersion = $Matches[1]
        Assert-OwnedDestination $destination $statePath $baselinePath $session.ToolId $installedBaseVersion
        if ([version]$version -lt [version]$installedBaseVersion) {
            Write-Result "NO_UPDATE_BASE_NEWER" $tag
            return
        }
        Assert-Before $script:Mutation "PREMUTATION_TIMEOUT"
        [IO.Directory]::CreateDirectory($skillsRoot) | Out-Null
        if ((Test-ReparseAtOrAbove $destination) -or (Test-ReparseAtOrAbove $statePath)) { throw "REPARSE" }
        $previousDestinationHash = Get-Fingerprint $destination
        $previousStateHash = Get-Fingerprint $statePath
        $transactionRoot = [IO.Path]::Combine($stateRoot, "transactions", $TransactionId)
        $stagingPath = [IO.Path]::Combine($transactionRoot, "staging")
        $previousPath = [IO.Path]::Combine($transactionRoot, "previous")

        $stateValue = [ordered]@{
            schema_version = 1
            target = "opencode"
            release_tag = $tag
            release_version = $version
            release_manifest_sha256 = $releaseManifestHash
            session_manifest_sha256 = $asset.manifest_sha256
            verified_at = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", [Globalization.CultureInfo]::InvariantCulture)
            tools = @([ordered]@{
                id = $session.ToolId
                destination = $destination
                ownership_marker = "session-tools-v1:opencode:$($session.ToolId)"
                files = @($session.Files)
            })
        }
        $stateBytes = ConvertTo-CanonicalJsonBytes $stateValue
        $expectedStateHash = Get-Sha256Bytes $stateBytes
        $canonical = New-Object Text.StringBuilder
        foreach ($file in $session.Files) { [void]$canonical.Append($file.path).Append([char]0).Append($file.sha256).Append("`n") }
        $expectedDestinationHash = Get-Sha256Bytes ((New-Object Text.UTF8Encoding($false)).GetBytes($canonical.ToString()))
        $journal = [ordered]@{
            schema_version = 1; target = "opencode"; transaction_id = $TransactionId; phase = "created"
            receipt_sha256 = $receiptHash; start_tick = $script:Start; mutation_cutoff_tick = $script:Mutation
            kill_tick = $script:Kill; hard_deadline_tick = $script:Deadline; stopwatch_frequency = $script:Frequency
            previous_destination_sha256 = $previousDestinationHash; previous_state_sha256 = $previousStateHash
            expected_staging_sha256 = $expectedDestinationHash; expected_destination_sha256 = $expectedDestinationHash
            expected_state_sha256 = $expectedStateHash; staging_path = $stagingPath; previous_path = $previousPath
            destination_path = $destination; state_path = $statePath
            operations = [ordered]@{
                move_destination_to_previous = [ordered]@{ intent = $false; applied = $false }
                move_staging_to_destination = [ordered]@{ intent = $false; applied = $false }
                write_state = [ordered]@{ intent = $false; applied = $false }
            }
        }
        $script:InMutation = $true
        Set-JournalPhase $journal "created"
        [IO.Directory]::CreateDirectory($stagingPath) | Out-Null
        if ($env:LLM_FOUNDATION_TEST_STOP_DURING_STAGING -ceq "empty") { [Diagnostics.Process]::GetCurrentProcess().Kill() }
        $firstStagingFile = $true
        foreach ($file in $session.Files) {
            $target = [IO.Path]::Combine($stagingPath, $file.path.Replace('/', [IO.Path]::DirectorySeparatorChar))
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
            $payload = [byte[]]$session.Payloads["tools/$($session.ToolId)/$($file.path)"]
            if ($firstStagingFile -and $env:LLM_FOUNDATION_TEST_STOP_DURING_STAGING -ceq "partial-bytes") {
                $partialLength = [Math]::Max(1, [int]($payload.Length / 2))
                [byte[]]$partialPayload = $payload[0..($partialLength - 1)]
                Write-DurableBytes $target $partialPayload
                [Diagnostics.Process]::GetCurrentProcess().Kill()
            }
            Write-DurableBytes $target $payload
            $firstStagingFile = $false
        }
        if ((Get-Fingerprint $stagingPath) -cne $expectedDestinationHash) { throw "STAGING" }
        Set-JournalPhase $journal "staged"
        Set-JournalPhase $journal "move_destination_intent"
        if ($previousDestinationHash -cne "absent") { [IO.Directory]::Move($destination, $previousPath) }
        if ($env:LLM_FOUNDATION_TEST_STOP_AFTER_MUTATION -ceq "move_destination") { [Diagnostics.Process]::GetCurrentProcess().Kill() }
        Set-JournalPhase $journal "move_destination_applied"
        Set-JournalPhase $journal "move_staging_intent"
        [IO.Directory]::Move($stagingPath, $destination)
        if ($env:LLM_FOUNDATION_TEST_STOP_AFTER_MUTATION -ceq "move_staging") { [Diagnostics.Process]::GetCurrentProcess().Kill() }
        Set-JournalPhase $journal "move_staging_applied"
        Set-JournalPhase $journal "state_write_intent"
        Write-DurableBytes $statePath $stateBytes
        if ($env:LLM_FOUNDATION_TEST_STOP_AFTER_MUTATION -ceq "write_state") { [Diagnostics.Process]::GetCurrentProcess().Kill() }
        Set-JournalPhase $journal "state_write_applied"
        Set-JournalPhase $journal "committed"
        Remove-Entry $previousPath
        Remove-EmptyTransactionRoot $transactionRoot
        [IO.File]::Delete($script:JournalPath)
        $script:InMutation = $false
        Write-Result "UPDATED" $tag
    }
    finally {
        if ($null -ne $script:LockStream) {
            try { $script:LockStream.Unlock(0, 1) } catch { }
            $script:LockStream.Dispose()
            $script:LockStream = $null
        }
    }
}

try {
    Invoke-Update
    exit 0
}
catch {
    $reason = [string]$_.Exception.Message
    if ($reason -eq "CONTRACT") { Write-Result "BLOCKED_INVALID_LAUNCHER_CONTRACT"; exit 64 }
    if ($script:InMutation -and [IO.File]::Exists($script:JournalPath)) {
        try {
            $profile = [IO.Path]::GetFullPath($env:USERPROFILE)
            $receipt = [IO.Path]::Combine($profile, ".llm-foundation", "bin", "opencode-managed.receipt.json")
            $null = Recover-ActiveTransaction $script:JournalPath (Get-Sha256File $receipt)
            Write-Result "RECOVERED_AFTER_ERROR"
            exit 0
        }
        catch { Write-Result "BLOCKED_SESSION_RECOVERY"; exit 65 }
    }
    if ($reason -in @("JOURNAL", "RECOVERY", "RECOVERY_TIMEOUT")) { Write-Result "BLOCKED_SESSION_RECOVERY"; exit 65 }
    if ($reason -eq "BLOCKED_MULTI_TOOL_ASSET") { Write-Result "BLOCKED_MULTI_TOOL_ASSET"; exit 0 }
    if ($reason -eq "BLOCKED_UNMANAGED_COLLISION") { Write-Result "BLOCKED_UNMANAGED_COLLISION"; exit 0 }
    if ($reason -eq "BLOCKED_MANAGED_DRIFT") { Write-Result "BLOCKED_MANAGED_DRIFT"; exit 0 }
    if ($reason -eq "PREMUTATION_TIMEOUT") { Write-Result "SKIPPED_TIMEOUT"; exit 0 }
    if ($reason -eq "GH_REQUIRED") { Write-Result "BLOCKED_GH_REQUIRED"; exit 0 }
    if ($reason -eq "NETWORK_UNAVAILABLE") { Write-Result "SKIPPED_OFFLINE"; exit 0 }
    Write-Result "SKIPPED_UNTRUSTED_UPDATE"
    exit 0
}
finally {
    if ($null -ne $script:LockStream) {
        try { $script:LockStream.Unlock(0, 1) } catch { }
        $script:LockStream.Dispose()
    }
    if ($script:TempRoot -and [IO.Directory]::Exists($script:TempRoot)) {
        try { [IO.Directory]::Delete($script:TempRoot, $true) } catch { }
    }
}
