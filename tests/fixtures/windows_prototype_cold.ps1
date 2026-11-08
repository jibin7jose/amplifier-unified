#requires -Version 5.1
<#
Controller-only cold-bootstrap driver. Invoke with ordinary -NoProfile -File,
never dot-source or change execution policy to admit either this or the installer.
The external controller owns child PATH/PSModulePath sanitization, Job containment,
timeouts, bounded private stdout/stderr capture, freshness comparison and cleanup.
Preflight/inspect JSON contains PRIVATE root addresses and process IDs: retain in
controller memory, not public logs/artifacts. Install forwards real installer output.
This file writes nothing itself and never invokes helpers, the app or a shortcut.
Inspection is a passive snapshot, NOT pinned filesystem identity or cleanup authority.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('preflight', 'install', 'inspect')]
    [string] $Action,
    [string] $Installer,
    [string] $ManifestUrl,
    [string] $ManifestSha256,
    [string] $Root
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
if ($MyInvocation.InvocationName -eq '.') {
    throw 'Use the literal driver file; dot-sourcing is not supported.'
}
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This driver requires native Windows.'
}

function Assert-WindowsPowerShell51 {
    if ($PSVersionTable.PSEdition -cne 'Desktop' -or
        $PSVersionTable.PSVersion.Major -ne 5 -or
        $PSVersionTable.PSVersion.Minor -ne 1 -or
        -not [Environment]::Is64BitProcess) {
        throw 'Use 64-bit Windows PowerShell 5.1.'
    }
}

function Get-SourceFacts {
    $installerHash = $null
    if ($Installer) {
        if ($Installer -notmatch '^[A-Za-z]:\\' -or
            -not [IO.Path]::IsPathRooted($Installer)) {
            throw 'Installer must be an absolute local script path.'
        }
        $item = Get-Item -LiteralPath $Installer -Force
        if ($item.PSIsContainer -or $item.Extension -ine '.ps1' -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Installer must be an existing regular PowerShell script.'
        }
        $installerHash = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    return [ordered]@{
        driverSha256 = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
        installerSha256 = $installerHash
    }
}

function Get-ColdPreflight {
    $windows = [Environment]::GetFolderPath('Windows')
    $system32 = Join-Path $windows 'System32'
    $expectedPath = $system32 + ';' + $windows
    $pathMatches = [string]::Equals($env:PATH, $expectedPath, [StringComparison]::OrdinalIgnoreCase)
    $refusals = @()
    if (-not $pathMatches) { $refusals += 'child-path-not-exact-system32-windows' }
    $commands = [ordered]@{}
    foreach ($name in @('uv', 'git', 'python', 'python3', 'py', 'curl', 'winget', 'wsl', 'pwsh')) {
        # Discover only. In particular, do not run curl, wsl, --version or --list.
        $found = @(Get-Command -Name $name -All -ErrorAction SilentlyContinue)
        $matches = @()
        foreach ($command in $found) {
            $osProvided = $false
            if ($command.CommandType -eq [Management.Automation.CommandTypes]::Application -and
                $name -in @('curl', 'wsl')) {
                $osProvided = [string]::Equals($command.Path, (Join-Path $system32 ($name + '.exe')),
                    [StringComparison]::OrdinalIgnoreCase)
            }
            # Desktop PowerShell ships a curl alias as well as possible OS curl.exe.
            if ($name -eq 'curl' -and
                $command.CommandType -eq [Management.Automation.CommandTypes]::Alias -and
                $command.Definition -ceq 'Invoke-WebRequest' -and
                $command.ResolvedCommand.ModuleName -ceq 'Microsoft.PowerShell.Utility') {
                $osProvided = $true
            }
            $matches += [ordered]@{
                commandType = $command.CommandType.ToString()
                osProvided = $osProvided
            }
            if ($name -notin @('curl', 'wsl') -or -not $osProvided) {
                $refusals += ('resolvable-prerequisite-' + $name)
            }
        }
        $commands[$name] = [ordered]@{
            resolvable = ($found.Count -gt 0)
            count = $found.Count
            matches = @($matches)
        }
    }
    $local = [Environment]::GetFolderPath('LocalApplicationData')
    if (-not $local -or -not [IO.Directory]::Exists($local)) {
        throw 'LocalApplicationData is unavailable; no root snapshot was taken.'
    }
    $roots = @(Get-ChildItem -LiteralPath $local -Force -Directory |
        Where-Object { $_.Name -cmatch '^amplifier-install-[0-9a-f]{32}$' } |
        Select-Object -ExpandProperty FullName)
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    try {
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        $elevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } finally { $identity.Dispose() }
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    $architectures = @(Get-CimInstance -ClassName Win32_Processor |
        Select-Object -ExpandProperty Architecture -Unique)
    # Match the UNMODIFIED installer gate rather than weakening it for hosted CI.
    if ($elevated) { $refusals += 'installer-requires-non-elevated-token' }
    if ([int]$os.BuildNumber -lt 22000 -or $os.ProductType -ne 1 -or
        $architectures.Count -ne 1 -or $architectures[0] -ne 9) {
        $refusals += 'installer-requires-windows-11-client-x64'
    }
    $profile = [ordered]@{
        userProfilePresent = -not [string]::IsNullOrWhiteSpace($env:USERPROFILE)
        homeDrivePresent = -not [string]::IsNullOrWhiteSpace($env:HOMEDRIVE)
        homePathPresent = -not [string]::IsNullOrWhiteSpace($env:HOMEPATH)
    }
    if (-not $profile.userProfilePresent -or -not $profile.homeDrivePresent -or -not $profile.homePathPresent) {
        $refusals += 'ordinary-profile-environment-missing'
    }
    if ($PSVersionTable.PSEdition -cne 'Desktop' -or
        $PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1 -or
        -not [Environment]::Is64BitProcess) {
        $refusals += 'requires-x64-windows-powershell-5.1'
    }
    # Elevated CI is reported, not mislabeled as ordinary-user qualification.
    return [ordered]@{
        schemaVersion = 1; action = 'preflight'
        ready = ($refusals.Count -eq 0)
        refusals = @($refusals | Select-Object -Unique)
        version = $PSVersionTable.PSVersion.ToString()
        edition = $PSVersionTable.PSEdition
        process64 = [Environment]::Is64BitProcess
        platform = [Environment]::OSVersion.Platform.ToString()
        osBuild = [int]$os.BuildNumber
        osProductType = [int]$os.ProductType
        processorArchitectures = @($architectures)
        elevated = $elevated
        effectivePolicy = (Get-ExecutionPolicy).ToString()
        scopes = @(Get-ExecutionPolicy -List | ForEach-Object {
            [ordered]@{ scope = $_.Scope.ToString(); policy = $_.ExecutionPolicy.ToString() }
        })
        pathIsExactSystem32Windows = $pathMatches
        commands = $commands
        profile = $profile
        roots = @($roots)
        localAppData = $local
        source = Get-SourceFacts
    }
}

function Assert-PrivateRoot {
    $local = [Environment]::GetFolderPath('LocalApplicationData')
    if (-not $Root -or $Root -cnotmatch '^[A-Za-z]:\\' -or
        -not [string]::Equals([IO.Path]::GetFullPath($Root), $Root, [StringComparison]::OrdinalIgnoreCase) -or
        -not [string]::Equals([IO.Path]::GetDirectoryName($Root), $local, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($Root) -cnotmatch '^amplifier-install-[0-9a-f]{32}$') {
        throw 'Inspection requires an exact direct-child bootstrap root under LocalApplicationData.'
    }
    # Reject reparse traversal, including ancestors, before reading root contents.
    $cursor = $Root
    while ($cursor) {
        $entry = Get-Item -LiteralPath $cursor -Force
        if (-not $entry.PSIsContainer -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Inspection refuses reparse or non-directory root traversal.'
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
    $acl = Get-Acl -LiteralPath $Root
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    try { $tokenUser = $identity.User.Value } finally { $identity.Dispose() }
    $ownerEqual = ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ceq $tokenUser)
    $raw = [Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorBinaryForm(), 0)
    if (-not $acl.AreAccessRulesProtected -or -not $ownerEqual -or
        $null -eq $raw.DiscretionaryAcl -or $raw.DiscretionaryAcl.Count -eq 0) {
        throw 'Inspection requires a protected, nonempty private DACL and TokenUser owner.'
    }
    $trusted = @($tokenUser, 'S-1-5-18', 'S-1-5-32-544',
        'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464', 'S-1-3-4')
    foreach ($ace in $raw.DiscretionaryAcl) {
        if ($ace -isnot [Security.AccessControl.CommonAce] -or $ace.IsCallback -or
            (([int]$ace.AceFlags -band (-bnot 0x1f)) -ne 0) -or
            $ace.AceQualifier -notin @([Security.AccessControl.AceQualifier]::AccessAllowed,
                [Security.AccessControl.AceQualifier]::AccessDenied)) {
            throw 'Inspection refuses unsupported ACL entries.'
        }
        if ($ace.AceQualifier -eq [Security.AccessControl.AceQualifier]::AccessAllowed -and
            $ace.AccessMask -ne 0 -and $ace.SecurityIdentifier.Value -notin $trusted) {
            throw 'Inspection refuses foreign access to the private root.'
        }
    }
    return [ordered]@{ protected = $true; ownerTokenUserEqual = $ownerEqual; private = $true }
}

function Get-RootItem([string] $Relative) {
    # All callers supply fixed source-owned relative names, never receipt paths.
    $path = Join-Path $Root $Relative
    $cursor = $Root
    foreach ($part in $Relative.Split('\')) {
        if (-not $part -or $part -in @('.', '..') -or $part -match '[:/]') {
            throw 'Invalid inspection-relative path.'
        }
        $cursor = Join-Path $cursor $part
        $entry = Get-Item -LiteralPath $cursor -Force
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Inspection refuses a reparse entry.'
        }
    }
    return Get-Item -LiteralPath $path -Force
}

function Read-RootJson([string] $Relative) {
    $item = Get-RootItem $Relative
    if ($item.PSIsContainer -or $item.Length -gt 1048576) {
        throw 'Inspection requires a bounded regular JSON receipt.'
    }
    $record = [IO.File]::ReadAllText($item.FullName, [Text.Encoding]::UTF8) | ConvertFrom-Json
    if ($null -eq $record -or $record -isnot [Management.Automation.PSCustomObject]) {
        throw 'Inspection receipt must be a JSON object.'
    }
    return $record
}

function Get-ColdInspection {
    $privateAcl = Assert-PrivateRoot
    $rootItem = Get-Item -LiteralPath $Root -Force
    $receipt = Read-RootJson 'prototype.json'
    foreach ($field in @('status', 'source', 'version', 'manifestSha256',
        'capsuleSha256', 'sourceMode', 'bootstrapUv', 'python', 'uv', 'git')) {
        if ($receipt.$field -isnot [string]) {
            throw 'Prototype receipt identity fields must be strings.'
        }
    }
    if ($receipt.schemaVersion -ne 1 -or $receipt.status -cne 'prototype' -or
        $receipt.source -cnotmatch '^[0-9a-f]{40}$' -or
        $receipt.version -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        $receipt.manifestSha256 -cnotmatch '^[0-9a-f]{64}$' -or
        $receipt.capsuleSha256 -cnotmatch '^[0-9a-f]{64}$' -or
        $receipt.sourceMode -notin @('pinned-vcs', 'offline-wheels')) {
        throw 'Invalid prototype receipt identity.'
    }
    $expectedHelpers = [ordered]@{
        uv = @{ directory = 'uv-0.12.18'; executable = 'uv.exe'; version = '0.12.18'
            sha256 = 'cae6a3bc25239f83dffb467a4b180508d9da23986c04639ebfa44e43e6a84bff'
            url = 'https://github.com/astral-sh/uv/releases/download/0.12.18/uv-x86_64-pc-windows-msvc.zip'
            license = 'LICENSE-MIT'; licenses = @('LICENSE-MIT', 'LICENSE-APACHE') }
        git = @{ directory = 'git-2.56.0.windows.4'; executable = 'cmd\git.exe'; version = '2.56.0.windows.4'
            sha256 = 'e3bf78351fe777fba47ddee5950082f8a98bb0f02604fdc7ba3ee2cb68ffa877'
            url = 'https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.4/MinGit-2.56.0.4-64-bit.zip'
            license = 'LICENSE.txt'; licenses = @('LICENSE.txt') }
    }
    $hashes = [ordered]@{}
    foreach ($relative in @('prototype.json', 'prototype-plan.json', 'capsule.zip',
        'capsule\capsule.json', 'capsule\launch.py', 'Launch Unified.lnk')) {
        $item = Get-RootItem $relative
        if ($item.PSIsContainer) { throw 'Expected a regular inspection file.' }
        $hashes[$relative] = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    if ($hashes['prototype-plan.json'] -cne $receipt.manifestSha256 -or
        $hashes['capsule.zip'] -cne $receipt.capsuleSha256) {
        throw 'Retained prototype input hashes differ from the receipt.'
    }
    $helperFacts = [ordered]@{}
    foreach ($name in $expectedHelpers.Keys) {
        $expected = $expectedHelpers[$name]
        $relativeExe = $expected.directory + '\' + $expected.executable
        $expectedPath = Join-Path $Root $relativeExe
        if ($receipt.$name -isnot [string] -or
            -not [string]::Equals($receipt.$name, $expectedPath, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Receipt helper is not the expected private executable.'
        }
        $helperItem = Get-RootItem $relativeExe
        if ($helperItem.PSIsContainer) { throw 'Private helper is not a regular file.' }
        $relativeReceipt = $expected.directory + '\bootstrap-receipt.json'
        $helper = Read-RootJson $relativeReceipt
        foreach ($field in @('version', 'sha256', 'url', 'executable', 'license')) {
            if ($helper.$field -isnot [string] -or $helper.$field -cne $expected[$field]) {
                throw 'Private helper receipt differs from the installer pin.'
            }
        }
        $archiveItem = Get-RootItem ($name + '.zip')
        if ($archiveItem.PSIsContainer) { throw 'Helper archive is not a regular file.' }
        $archiveHash = (Get-FileHash -LiteralPath $archiveItem.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($archiveHash -cne $helper.sha256) { throw 'Retained helper archive hash mismatch.' }
        $licenses = [ordered]@{}
        foreach ($notice in $expected.licenses) {
            $relative = $expected.directory + '\' + $notice
            # Missing notices remain observable facts, not synthesized receipts.
            $exists = Test-Path -LiteralPath (Join-Path $Root $relative)
            $licenses[$notice] = [bool]$exists
            if ($exists) {
                $licenseItem = Get-RootItem $relative
                if ($licenseItem.PSIsContainer) { throw 'License is not a regular file.' }
                $hashes[$relative] = (Get-FileHash -LiteralPath $licenseItem.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
        $hashes[$relativeReceipt] = (Get-FileHash -LiteralPath (Get-RootItem $relativeReceipt).FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $hashes[$relativeExe] = (Get-FileHash -LiteralPath $helperItem.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $hashes[$name + '.zip'] = $archiveHash
        $helperFacts[$name] = [ordered]@{
            path = $expectedPath; version = $helper.version; archiveSha256 = $archiveHash
            bootstrapReceiptExists = $true; licensesExist = $licenses
        }
    }
    $pythonPath = Join-Path $Root 'host\Scripts\python.exe'
    if ($receipt.python -isnot [string] -or
        -not [string]::Equals($receipt.python, $pythonPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Receipt Python is not the private host interpreter.'
    }
    $pythonItem = Get-RootItem 'host\Scripts\python.exe'
    if ($pythonItem.PSIsContainer) { throw 'Private Python is not a regular file.' }
    if (($receipt.sourceMode -ceq 'pinned-vcs' -and $receipt.bootstrapUv -cne '0.12.18') -or
        ($receipt.sourceMode -ceq 'offline-wheels' -and $receipt.bootstrapUv -cne 'reused-or-private')) {
        throw 'Prototype bootstrap uv identity does not match its source mode.'
    }
    # CreateShortcut reads an EXISTING .lnk only. Never call Save or launch it.
    $link = Get-RootItem 'Launch Unified.lnk'
    $shell = $null
    $shortcut = $null
    try {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($link.FullName)
        $shortcutFacts = [ordered]@{
            target = $shortcut.TargetPath; arguments = $shortcut.Arguments
            workingDirectory = $shortcut.WorkingDirectory
        }
    } finally {
        if ($null -ne $shortcut) { $null = [Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut) }
        if ($null -ne $shell) { $null = [Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell) }
    }
    $entries = @(Get-ChildItem -LiteralPath $Root -Force | ForEach-Object {
        [ordered]@{ name = $_.Name; isDirectory = [bool]$_.PSIsContainer
            reparsePoint = [bool]($_.Attributes -band [IO.FileAttributes]::ReparsePoint) }
    })
    $processIds = @(Get-CimInstance -ClassName Win32_Process | Where-Object {
        $_.ProcessId -ne $PID -and
        (($_.CommandLine -and $_.CommandLine.IndexOf($Root, [StringComparison]::OrdinalIgnoreCase) -ge 0) -or
         ($_.ExecutablePath -and $_.ExecutablePath.IndexOf($Root, [StringComparison]::OrdinalIgnoreCase) -ge 0))
    } | ForEach-Object { [int]$_.ProcessId })
    return [ordered]@{
        schemaVersion = 1; action = 'inspect'; root = $Root
        rootCreationTimeUtc = $rootItem.CreationTimeUtc.ToString('o')
        rootReparsePoint = [bool]($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint)
        acl = $privateAcl
        receipt = [ordered]@{
            schemaVersion = $receipt.schemaVersion; status = $receipt.status
            source = $receipt.source; version = $receipt.version; sourceMode = $receipt.sourceMode
            manifestSha256 = $receipt.manifestSha256; capsuleSha256 = $receipt.capsuleSha256
            bootstrapUv = $receipt.bootstrapUv; python = $pythonPath
        }
        helpers = $helperFacts; hashes = $hashes; shortcut = $shortcutFacts
        firstLevelEntries = @($entries)
        rootReferencedProcessCount = $processIds.Count
        rootReferencedProcessIds = @($processIds)
        source = Get-SourceFacts
    }
}

switch ($Action) {
    'preflight' {
        $facts = Get-ColdPreflight
        $facts | ConvertTo-Json -Depth 6 -Compress
        # The observation can succeed while readiness is false. The controller
        # persists these facts and refuses installation, never labels it PASS.
    }
    'install' {
        Assert-WindowsPowerShell51
        if (-not $Installer -or -not $ManifestUrl -or $ManifestSha256 -cnotmatch '^[0-9a-f]{64}$') {
            throw 'Installation requires an absolute installer, manifest URL and lowercase SHA-256.'
        }
        $null = Get-SourceFacts
        if ((Get-ExecutionPolicy).ToString() -eq 'Restricted') {
            throw 'Execution policy refuses script installation; no policy change is permitted.'
        }
        # A script call has its own terminating errors and may leave this unset.
        # Reset it first so an inherited native exit code cannot invent failure.
        $global:LASTEXITCODE = 0
        & $Installer -PrototypeManifestUrl $ManifestUrl -PrototypeManifestSha256 $ManifestSha256 -NoOpen
        if ($LASTEXITCODE -ne 0) { throw 'The real installer returned a nonzero exit code.' }
    }
    'inspect' {
        Get-ColdInspection | ConvertTo-Json -Depth 6 -Compress
    }
}