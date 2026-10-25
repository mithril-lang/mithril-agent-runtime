# Mithril Agent Windows installer. Download this file from an owned, immutable
# runtime revision and run it with -File. The child bootstrap is hash-pinned and
# must contain the Mithril-specific origin and mirror guards before execution.
[CmdletBinding(PositionalBinding=$false)]
param(
    [string]$HermesHome = $(if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $HOME '.mithril' }),
    [string]$InstallDir = '',
    [switch]$IncludeDesktop
)

$ErrorActionPreference = 'Stop'
$script:RunAsFile = $MyInvocation.InvocationName -ne '.'
$repository = 'https://github.com/mithril-lang/mithril-agent-runtime.git'
$branch = 'main'
$revision = '762c7e8cb0a7cb7f2866e72ccc9c773680e2afa3'
$installerSha256 = '5204fb92ced8b94af58e9ce37151cbbbc489b3b03ca81830a57362362d3d20da'
$defaultHome = Join-Path $HOME '.mithril'
$legacyHome = Join-Path $HOME '.hermes'

if (-not [System.IO.Path]::IsPathRooted($HermesHome)) {
    throw 'Mithril Agent profile path must be absolute.'
}
if (-not $InstallDir) { $InstallDir = Join-Path $HermesHome 'agent' }
if (-not [System.IO.Path]::IsPathRooted($InstallDir)) {
    throw 'Mithril Agent runtime path must be absolute.'
}

$download = Join-Path $env:TEMP ("mithril-agent-install-" + [guid]::NewGuid().ToString() + '.download')
$installer = Join-Path $env:TEMP ("mithril-agent-install-" + [guid]::NewGuid().ToString() + '.ps1')
$oldMithrilAgent = [Environment]::GetEnvironmentVariable('MITHRIL_AGENT', 'Process')
$oldRepoUrl = [Environment]::GetEnvironmentVariable('HERMES_REPO_URL', 'Process')
$oldHermesHome = [Environment]::GetEnvironmentVariable('HERMES_HOME', 'Process')
$exitCode = 1

try {
    try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
    $url = "https://raw.githubusercontent.com/mithril-lang/mithril-agent-runtime/$revision/scripts/install.ps1"
    Invoke-WebRequest -Uri $url -UseBasicParsing -OutFile $download
    $actualSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $download).Hash.ToLowerInvariant()
    if ($actualSha256 -ne $installerSha256) {
        throw 'Pinned installer checksum mismatch; refusing to execute.'
    }

    $text = [System.IO.File]::ReadAllText($download, [System.Text.Encoding]::UTF8).TrimStart([char]0xFEFF)
    if (-not $text.Contains('MITHRIL_AGENT -ne "1" -and $MirrorUrl') -or
        -not $text.Contains('MITHRIL_AGENT -eq "1" -and $RepoUrl') -or
        -not $text.Contains('Mithril Agent installer requires the Mithril-owned runtime repository')) {
        throw 'Pinned installer lacks Mithril origin and mirror guards; no runtime code was executed.'
    }

    # Preserve existing Hermes state through a junction, as the Unix entrypoint
    # does with a symlink. Never copy or replace the original profile directory.
    if (-not $PSBoundParameters.ContainsKey('HermesHome') -and -not $env:HERMES_HOME -and -not (Test-Path -LiteralPath $defaultHome)) {
        $legacyState = @(
            (Join-Path $legacyHome 'config.yaml'),
            (Join-Path $legacyHome 'state.db'),
            (Join-Path $legacyHome 'profiles'),
            (Join-Path $legacyHome 'active_profile')
        ) | Where-Object { Test-Path -LiteralPath $_ }
        if ($legacyState.Count -gt 0 -and (Test-Path -LiteralPath $legacyHome -PathType Container)) {
            New-Item -ItemType Junction -Path $defaultHome -Target $legacyHome | Out-Null
        }
    }

    [System.IO.File]::WriteAllText($installer, $text, (New-Object System.Text.UTF8Encoding $true))
    $env:MITHRIL_AGENT = '1'
    $env:HERMES_REPO_URL = $repository
    $env:HERMES_HOME = $HermesHome

    $engineName = if ($PSVersionTable.PSEdition -eq 'Core') { 'pwsh.exe' } else { 'powershell.exe' }
    $engine = Join-Path $PSHOME $engineName
    if (-not (Test-Path -LiteralPath $engine -PathType Leaf)) {
        throw "Could not locate the current PowerShell executable: $engine"
    }
    $arguments = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-NonInteractive', '-File', $installer,
        '-HermesHome', $HermesHome, '-InstallDir', $InstallDir,
        '-Branch', $branch, '-Commit', $revision,
        '-NonInteractive', '-SkipBrowser', '-SkipComputerUse'
    )
    if ($IncludeDesktop) { $arguments += '-IncludeDesktop' }
    & $engine @arguments
    $exitCode = $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine("Mithril Agent: $($_.Exception.Message)")
    $exitCode = 1
} finally {
    Remove-Item -Force -ErrorAction SilentlyContinue $download, $installer
    [Environment]::SetEnvironmentVariable('MITHRIL_AGENT', $oldMithrilAgent, 'Process')
    [Environment]::SetEnvironmentVariable('HERMES_REPO_URL', $oldRepoUrl, 'Process')
    [Environment]::SetEnvironmentVariable('HERMES_HOME', $oldHermesHome, 'Process')
}

if ($script:RunAsFile) { exit $exitCode }
if ($exitCode -ne 0) { throw "Mithril Agent installation failed with exit code $exitCode." }
Write-Output "Mithril Agent installed at $InstallDir. Run the mithril command to sign in or configure a provider."
