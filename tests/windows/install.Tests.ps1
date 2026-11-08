#requires -Version 5.1
# Self-contained, offline fixtures: no Pester, downloads or installed app needed.
# Run separately in Windows PowerShell 5.1 AND PowerShell 7 on native Windows.
param([Parameter(Mandatory = $true)][string] $PythonExecutable)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
# A version-only preflight cannot detect cross-edition module shadowing.
# Import by name so failure reports the actual module-loading cause.
Import-Module Microsoft.PowerShell.Security -ErrorAction Stop
$installer = Join-Path $PSScriptRoot '..\..\scripts\install.ps1'
$tokens = $null
$errors = $null
$null = [Management.Automation.Language.Parser]::ParseFile(
    $installer, [ref]$tokens, [ref]$errors)
if ($errors.Count -ne 0) { throw ($errors | Out-String) }
. $installer

function Assert-Equal($Actual, $Expected) {
    if ($Actual -cne $Expected) { throw "Fixture assertion failed: expected [$Expected], got [$Actual]." }
}
function Assert-Refused([scriptblock] $Action, [string] $Pattern) {
    try { & $Action } catch {
        if ($_.Exception.Message -notmatch $Pattern) { throw }
        return
    }
    throw "Fixture expected refusal matching $Pattern."
}
function Assert-SharingRefused([string] $Case, [scriptblock] $Action) {
    try { & $Action } catch {
        $code = $_.Exception.GetBaseException().HResult -band 65535
        if ($code -notin @(5, 32, 33)) {
            throw "Fixture [$Case] expected access/sharing refusal, got error $code."
        }
        return
    }
    throw "Fixture [$Case] expected an access/sharing refusal; operation succeeded."
}
function Assert-PrivateFileAcl([string] $Path) {
    $acl = Get-Acl -LiteralPath $Path
    $owner = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $owner -or
        -not $acl.AreAccessRulesProtected) { throw 'Fixture file lacks exact TokenUser/protected DACL.' }
    $seen = @()
    foreach ($rule in $acl.Access) {
        $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if ($sid -notin @($owner, 'S-1-5-18') -or $rule.IsInherited -or
            $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
            $rule.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl) {
            throw 'Fixture file lacks explicit owner/SYSTEM-only full control.'
        }
        $seen += $sid
    }
    if ($seen.Count -ne 2 -or $seen -notcontains $owner -or $seen -notcontains 'S-1-5-18') {
        throw 'Fixture file lacks the two expected trustees.'
    }
}

$root = New-PrivateInstallDirectory
$fixtureStorage = $script:InstallStorage
try {
    # P3: parse the displayed command as PowerShell source, without executing it.
    $apostrophePath = "C:\Users\O'Brien\space " + [char]0x03B2 + '\amplifier-unified.exe'
    $command = Format-PowerShellLaunchCommand $apostrophePath
    $commandTokens = $null
    $commandErrors = $null
    $commandAst = [Management.Automation.Language.Parser]::ParseInput(
        $command, [ref]$commandTokens, [ref]$commandErrors)
    Assert-Equal $commandErrors.Count 0
    $commands = @($commandAst.FindAll({
        param($node) $node -is [Management.Automation.Language.CommandAst]
    }, $true))
    Assert-Equal $commands.Count 1
    Assert-Equal $commands[0].InvocationOperator ([Management.Automation.Language.TokenKind]::Ampersand)
    Assert-Equal $commands[0].CommandElements.Count 1
    Assert-Equal $commands[0].CommandElements[0].Value $apostrophePath

    # P1: a normal (not reparse) ancestor with foreign mutation must be refused
    # before storage/log/download writes. A safe read/traverse grant is accepted.
    $canary = Join-Path $root 'outside-canary.txt'
    Write-PrivateInstallText $canary 'outside remains unchanged'
    $foreign = New-Object Security.Principal.SecurityIdentifier('S-1-5-21-111111111-222222222-333333333-4242')
    foreach ($rights in @('DeleteSubdirectoriesAndFiles', 'WriteAttributes', 'ChangePermissions',
        'TakeOwnership', 'WriteData', 'WriteExtendedAttributes', 'Delete', 'ReadAndExecute')) {
        $ancestor = Join-Path $root ('ancestor-' + $rights)
        $null = [IO.Directory]::CreateDirectory($ancestor)
        $acl = Get-Acl -LiteralPath $ancestor
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($foreign, $rights, 'Allow')
        $acl.AddAccessRule($rule)
        Set-Acl -LiteralPath $ancestor -AclObject $acl
        if ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Mutation fixture unexpectedly became a reparse point.'
        }
        if ($rights -eq 'ReadAndExecute') {
            $safeContext = New-Object UnifiedBootstrap.InstallStorage($ancestor)
            try { $safeContext.Validate() } finally { $safeContext.Dispose() }
        } else {
            Assert-Refused { $unexpected = New-Object UnifiedBootstrap.InstallStorage($ancestor) } 'Foreign mutation'
            Assert-Equal (@(Get-ChildItem -LiteralPath $ancestor -Force).Count) 0
        }
        Assert-Equal ([IO.File]::ReadAllText($canary)) 'outside remains unchanged'
    }
    $ancestor = Join-Path $root 'pinned-ancestor'
    $null = [IO.Directory]::CreateDirectory($ancestor)
    $pinned = New-Object UnifiedBootstrap.InstallStorage($ancestor)
    try {
        Assert-SharingRefused 'pinned-ancestor-rename' { [IO.Directory]::Move($ancestor, ($ancestor + '-replacement')) }
        Assert-SharingRefused 'pinned-private-root-rename' { [IO.Directory]::Move($pinned.Root, ($pinned.Root + '-replacement')) }
        $pinned.Validate()
    } finally { $pinned.Dispose() }
    Assert-Equal ([IO.File]::ReadAllText($canary)) 'outside remains unchanged'

    $name = 'amplifier_unified-0.44.0-py3-none-any.whl'
    $hash = 'a' * 64
    Assert-Equal (Get-ExactChecksum "$hash  $name`n" $name) $hash
    Assert-Equal (Get-ExactChecksum "$hash *$name`r`n" $name) $hash
    Assert-Refused { Get-ExactChecksum "$hash  $name`n$hash  $name" $name } 'duplicate'
    Assert-Refused { Get-ExactChecksum "$hash  not-$name" $name } 'Missing'
    Assert-Refused { Get-ExactChecksum "broken  $name" $name } 'Malformed'
    Assert-Refused { Get-ExactChecksum "$hash  ../$name" $name } 'Malformed'
    # Inspect creation security while the stream is EMPTY, before the first
    # payload byte. Inherited DACLs do not guarantee the TokenUser owner.
    $part = Join-Path $root 'stream.part'
    $stream = $fixtureStorage.CreateFile($part)
    $createdHandle = $stream.SafeFileHandle
    try {
        Assert-Equal $stream.Length 0
        Assert-Equal $createdHandle.IsClosed $false
        Assert-PrivateFileAcl $part
        $bytes = [Text.Encoding]::UTF8.GetBytes('private stream')
        $payload = New-Object IO.MemoryStream(,$bytes)
        try { $payload.CopyTo($stream) } finally { $payload.Dispose() }
        $stream.Flush($true)
    } finally { $stream.Dispose() }
    # Distinguish stream ownership from directory sharing: the stream owns its
    # SafeFileHandle on BOTH .NET editions. Inspect/Verify do not retain this
    # newly created file as a pin; only its parent remains pinned at this point.
    Assert-Equal $createdHandle.IsClosed $true
    Assert-Equal $stream.CanWrite $false
    Assert-Equal ([IO.File]::ReadAllText($part)) 'private stream'
    $published = Join-Path $root 'stream.bin'
    Assert-SharingRefused 'absolute-file-move-parent-pin' { [IO.File]::Move($part, $published) }
    # Exercise the actual same-directory publication while every directory pin
    # remains held. No retry, release/reacquire, broader sharing or GC workaround.
    $fixtureStorage.PublishFile($part, $published)
    Assert-Equal ([IO.File]::Exists($part)) $false
    $fixtureStorage.HoldFile($published, $false)
    $fixtureStorage.Validate()
    Assert-PrivateFileAcl $published
    Assert-Equal ([IO.File]::ReadAllText($published)) 'private stream'
    Assert-Refused { $unexpected = $fixtureStorage.CreateFile($published) } 'already exists'
    Assert-Equal ([IO.File]::ReadAllText($published)) 'private stream'
    # No-replace is tested against an UNPINNED private destination too: failure
    # must come from the rename, not a pre-existing target's no-DELETE sharing.
    $candidate = Join-Path $root 'collision.part'
    $winner = Join-Path $root 'collision.bin'
    foreach ($path in @($candidate, $winner)) {
        $stream = $fixtureStorage.CreateFile($path)
        try {
            $bytes = [Text.Encoding]::UTF8.GetBytes([IO.Path]::GetFileName($path))
            $stream.Write($bytes, 0, $bytes.Length)
            $stream.Flush($true)
        } finally { $stream.Dispose() }
    }
    Assert-Refused { $fixtureStorage.PublishFile($candidate, $winner) } 'already exists'
    Assert-Equal ([IO.File]::ReadAllText($candidate)) 'collision.part'
    Assert-Equal ([IO.File]::ReadAllText($winner)) 'collision.bin'
    Assert-Refused { $fixtureStorage.PublishFile($published, $candidate) } 'already pinned'
    $missingLog = Join-Path $root 'missing.log'
    Assert-Refused { Write-PrivateInstallText $missingLog 'must not create' -Append } 'pinned mutable'
    Assert-Equal ([IO.File]::Exists($missingLog)) $false
    $body = Join-Path $root 'body'
    Write-PrivateInstallText $body 'unchanged'
    Assert-PrivateFileAcl $body
    Assert-Refused { Write-PrivateInstallText $body 'must not replace' } 'already exists'
    Assert-Refused { Write-PrivateInstallText $body 'must not append' -Append } 'pinned mutable'
    Assert-Refused { Assert-FileChecksum $body ('0' * 64) } 'mismatch'
    Assert-FileChecksum $body (Get-FileHash -LiteralPath $body -Algorithm SHA256).Hash.ToLowerInvariant()
    Assert-SharingRefused 'hashed-file-append' { [IO.File]::AppendAllText($body, 'must not reach hashed bytes') }
    Assert-SharingRefused 'hashed-file-rename' { [IO.File]::Move($body, ($body + '-replacement')) }
    Assert-Equal ([IO.File]::ReadAllText($body)) 'unchanged'

    # Real Windows extraction, including Unicode/space paths and zip-slip cases.
    Add-Type -AssemblyName System.IO.Compression -ErrorAction Stop
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    foreach ($entryName in @('ok.txt', '../escape.txt', '/absolute.txt', 'C:/drive.txt',
        'folder/file:stream', 'CON.txt', 'folder/trailing.')) {
        $archivePath = Join-Path $root ([Guid]::NewGuid().ToString('N') + '.zip')
        $archiveStream = $fixtureStorage.CreateFile($archivePath)
        try {
            Assert-PrivateFileAcl $archivePath
            $archive = New-Object IO.Compression.ZipArchive($archiveStream, [IO.Compression.ZipArchiveMode]::Create, $true)
            try {
                $entry = $archive.CreateEntry($entryName)
                $writer = New-Object IO.StreamWriter($entry.Open())
                try { $writer.Write('fixture') } finally { $writer.Dispose() }
            } finally { $archive.Dispose() }
        } finally { $archiveStream.Dispose() }
        $fixtureStorage.HoldFile($archivePath, $false)
        $destination = Join-Path $root ('space ' + [char]0x03B2 + ' ' + [Guid]::NewGuid().ToString('N'))
        if ($entryName -eq 'ok.txt') {
            Expand-ContainedZip $archivePath $destination
            Assert-Equal ([IO.File]::ReadAllText((Join-Path $destination 'ok.txt'))) 'fixture'
            Assert-PrivateFileAcl (Join-Path $destination 'ok.txt')
            Assert-Refused { Expand-ContainedZip $archivePath $destination } 'already exists'
            Assert-SharingRefused 'extraction-root-rename' { [IO.Directory]::Move($destination, ($destination + '-replacement')) }
            $fixtureStorage.Validate()
        } else {
            Assert-Refused { Expand-ContainedZip $archivePath $destination } 'Unsafe|escaped'
        }
    }
    # Exact extraction creation refuses even a pre-existing ordinary safe root.
    $collision = Join-Path $root 'extraction-collision'
    $fixtureStorage.CreateDirectory($collision)
    Write-PrivateInstallText (Join-Path $collision 'keep.txt') 'never adopted'
    Assert-Refused { Expand-ContainedZip $archivePath $collision } 'already exists'
    Assert-Equal ([IO.File]::ReadAllText((Join-Path $collision 'keep.txt'))) 'never adopted'
    $junctionTarget = Join-Path $root 'junction-target'
    $junction = Join-Path $root 'junction'
    $fixtureStorage.CreateDirectory($junctionTarget)
    Write-PrivateInstallText (Join-Path $junctionTarget 'canary.txt') 'junction target unchanged'
    # CPython's native junction fixture requires no symlink privilege or shell.
    & $PythonExecutable -I -S -c 'import _winapi,sys; _winapi.CreateJunction(sys.argv[1],sys.argv[2])' $junctionTarget $junction
    if ($LASTEXITCODE -ne 0) { throw 'Native junction fixture could not be created; not a pass.' }
    try {
        Assert-Refused { $unexpected = New-Object UnifiedBootstrap.InstallStorage($junction) } 'reparse'
        Assert-Refused { Expand-ContainedZip $archivePath $junction } 'already exists'
        Assert-Equal ([IO.File]::ReadAllText((Join-Path $junctionTarget 'canary.txt'))) 'junction target unchanged'
    } finally { [IO.Directory]::Delete($junction) }
    if (Test-Path -LiteralPath (Join-Path $root 'escape.txt')) { throw 'ZIP escaped destination.' }
    foreach ($unsafeKind in @('case-collision', 'symlink')) {
        $archivePath = Join-Path $root "$unsafeKind.zip"
        $archiveStream = $fixtureStorage.CreateFile($archivePath)
        try {
            $archive = New-Object IO.Compression.ZipArchive($archiveStream, [IO.Compression.ZipArchiveMode]::Create, $true)
            try {
                if ($unsafeKind -eq 'case-collision') {
                    $null = $archive.CreateEntry('same.txt')
                    $null = $archive.CreateEntry('SAME.txt')
                } else {
                    $entry = $archive.CreateEntry('link')
                    $entry.ExternalAttributes = (40960 -shl 16)
                }
            } finally { $archive.Dispose() }
        } finally { $archiveStream.Dispose() }
        $fixtureStorage.HoldFile($archivePath, $false)
        $destination = Join-Path $root $unsafeKind
        Assert-Refused { Expand-ContainedZip $archivePath $destination } 'Unsafe|duplicated'
        Assert-Equal (@(Get-ChildItem -LiteralPath $destination).Count) 0
    }

    $support = [pscustomobject]@{
        schemaVersion = 1; release = 'v0.44.0'; status = 'qualified'
        os = 'windows-11'; architecture = 'x86_64'; python = 'cpython-3.13'
        powershell = @('5.1', '7'); nativePackages = @('amplifier-core')
        hostConstraints = 'windows-host-constraints.txt'; defaultGraph = 'windows-default-graph.json'
    }
    $null = Read-NativeSupport $support '0.44.0'
    foreach ($field in @('release', 'status', 'os', 'architecture', 'python', 'hostConstraints')) {
        $original = $support.$field
        $support.$field = 'unsupported'
        Assert-Refused { Read-NativeSupport $support '0.44.0' } 'not qualified|Invalid'
        $support.$field = $original
    }
    Assert-Refused { Assert-NativeActivationContract } 'integration is pending'

    # Real child process: Unicode/space cwd, CRT argv quoting, anonymous isolation.
    $script:InstallWork = Join-Path $root ('space ' + [char]0x03B2)
    $fixtureStorage.CreateDirectory($script:InstallWork)
    $script:InstallLog = Join-Path $root 'child.log'
    Write-PrivateInstallText $script:InstallLog '' -Mutable
    Assert-PrivateFileAcl $script:InstallLog
    Write-PrivateInstallText $script:InstallLog 'first' -Append
    Write-PrivateInstallText $script:InstallLog 'second' -Append
    Assert-Equal ([IO.File]::ReadAllText($script:InstallLog)) 'firstsecond'
    Assert-PrivateFileAcl $script:InstallLog
    $script:InstallStage = 'fixture'
    $script:UseAnonymous = $true
    # An executable shadow in an unsafe PATH-like directory is refused before
    # even a version probe. It need not be a runnable binary for this falsifier.
    # Create BOTH parent and leaf privately, then release this separate context
    # before tampering with the parent. Default directory/file ownership would
    # confound the intended foreign-mutation refusal on an elevated token.
    $shadowStorage = New-Object UnifiedBootstrap.InstallStorage($root)
    $shadowParent = $shadowStorage.Root
    $shadow = Join-Path $shadowParent 'shadow.exe'
    try {
        $parentAcl = Get-Acl -LiteralPath $shadowParent
        Assert-Equal ($parentAcl.GetOwner([Security.Principal.SecurityIdentifier]).Value) ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value)
        Assert-Equal $parentAcl.AreAccessRulesProtected $true
        $stream = $shadowStorage.CreateFile($shadow)
        try {
            Assert-Equal $stream.Length 0
            Assert-PrivateFileAcl $shadow
            $bytes = [Text.Encoding]::UTF8.GetBytes('not executable')
            $stream.Write($bytes, 0, $bytes.Length)
            $stream.Flush($true)
        } finally { $stream.Dispose() }
        # Positive validator control without executing the synthetic payload.
        $shadowStorage.TrustExecutable($shadow)
        $shadowStorage.Validate()
    } finally { $shadowStorage.Dispose() }
    $parentAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($foreign, 'WriteData', 'Allow')))
    Set-Acl -LiteralPath $shadowParent -AclObject $parentAcl
    Assert-PrivateFileAcl $shadow
    Assert-Refused { Invoke-InstallChild $shadow @('--version') @{} } 'Foreign mutation'
    $appendShadow = Join-Path $root 'append-shadow.exe'
    # Start from exact valid ownership; only the deliberate foreign append grant
    # below makes this negative fixture unsafe. Do not retain it as a valid pin.
    $stream = $fixtureStorage.CreateFile($appendShadow)
    try {
        Assert-PrivateFileAcl $appendShadow
        $bytes = [Text.Encoding]::UTF8.GetBytes('not executable')
        $stream.Write($bytes, 0, $bytes.Length)
    } finally { $stream.Dispose() }
    $acl = Get-Acl -LiteralPath $appendShadow
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($foreign, 'AppendData', 'Allow')))
    Set-Acl -LiteralPath $appendShadow -AclObject $acl
    Assert-Refused { Invoke-InstallChild $appendShadow @('--version') @{} } 'Foreign mutation'
    Assert-Equal ([IO.File]::ReadAllText($canary)) 'outside remains unchanged'
    # The CI interpreter's external tool-cache traversal need not satisfy the
    # bootstrap execution policy. Do not weaken that policy or copy an untrusted
    # runtime into private storage. Select the already-required installed PS7
    # host, then require the SAME validator to accept its absolute path below:
    # discovery is NOT trust. Do not assume component-store system executables
    # meet the validator's single-link rule. The private script's oracle reads
    # native CRT argv, NOT either PowerShell edition's parsed script arguments.
    $probeHost = (Get-Command 'pwsh.exe' -CommandType Application -ErrorAction Stop).Source
    $probeFile = Join-Path $script:InstallWork 'crt-probe.ps1'
    $probe = @'
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class OfflineCrt {
    [StructLayout(LayoutKind.Sequential)] struct Startup { public int newmode; }
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport("msvcrt.dll", EntryPoint="__wgetmainargs", ExactSpelling=true,
        CallingConvention=CallingConvention.Cdecl)]
    static extern int GetMainArgs(out int count, out IntPtr argv, out IntPtr env,
        int expandWildcards, ref Startup startup);
    public static string[] Read() {
        int count; IntPtr argv, env; var startup = new Startup();
        if (GetMainArgs(out count, out argv, out env, 0, ref startup) != 0 ||
            count < 6 || count > 64 || argv == IntPtr.Zero)
            throw new InvalidOperationException("Native CRT argv unavailable.");
        var values = new string[count];
        for (int i=0; i<count; i++)
            values[i] = Marshal.PtrToStringUni(Marshal.ReadIntPtr(argv, i * IntPtr.Size));
        return values;
    }
}
"@
$raw = [OfflineCrt]::Read()
if ($raw[1] -cne '-NoProfile' -or $raw[2] -cne '-NonInteractive' -or
    $raw[3] -cne '-File' -or $raw[4] -cne $PSCommandPath -or
    $raw[5] -cne '--fixture-values') { throw 'Native CRT probe prefix mismatch.' }
ConvertTo-Json -Compress -InputObject @{
    parser = 'msvcrt.__wgetmainargs'; values = @($raw | Select-Object -Skip 6)
    cwd = [Environment]::CurrentDirectory
    globalConfig = $env:GIT_CONFIG_GLOBAL; anonymous = $env:AMPLIFIER_GITHUB_ACCESS
    tokenAbsent = -not (Test-Path Env:GH_TOKEN)
}
'@
    Write-PrivateInstallText $probeFile $probe
    Assert-PrivateFileAcl $probeFile
    $probeHash = (Get-FileHash -LiteralPath $probeFile -Algorithm SHA256).Hash.ToLowerInvariant()
    Assert-FileChecksum $probeFile $probeHash
    $fixtureStorage.TrustExecutable($probeHost)
    $fixtureStorage.Validate()
    # Both outer editions launch this same installed PS7 host. Restrict only THAT
    # child's module search to its own built-ins; never edit the parent's env.
    $probeEnvironment = @{ PSModulePath = Join-Path ([IO.Path]::GetDirectoryName($probeHost)) 'Modules' }
    $beforePath = $env:PATH
    $beforeModules = $env:PSModulePath
    $beforeToken = $env:GH_TOKEN
    $beforeConfig = $env:GIT_CONFIG_GLOBAL
    try {
        $env:GH_TOKEN = 'synthetic-fixture-token'
        $env:GIT_CONFIG_GLOBAL = 'synthetic-user-config'
        $values = @('space value', ('unicode ' + [char]0x03B2), 'quote"value', 'trailing\', '')
        $result = Invoke-InstallChild $probeHost (@('-NoProfile', '-NonInteractive', '-File', $probeFile, '--fixture-values') + $values) $probeEnvironment
        $observed = $result | ConvertFrom-Json
        Assert-Equal $observed.parser 'msvcrt.__wgetmainargs'
        Assert-Equal $observed.cwd $script:InstallWork
        Assert-Equal $observed.globalConfig 'NUL'
        Assert-Equal $observed.anonymous 'anonymous'
        Assert-Equal $observed.tokenAbsent $true
        Assert-Equal $env:GH_TOKEN 'synthetic-fixture-token'
        Assert-Equal $env:GIT_CONFIG_GLOBAL 'synthetic-user-config'
        Assert-Equal $env:PATH $beforePath
        Assert-Equal (@($observed.values).Count) $values.Count
        for ($i = 0; $i -lt $values.Count; $i++) { Assert-Equal $observed.values[$i] $values[$i] }
    } finally {
        $env:GH_TOKEN = $beforeToken
        $env:GIT_CONFIG_GLOBAL = $beforeConfig
    }
    Assert-Equal $env:GH_TOKEN $beforeToken
    Assert-Equal $env:GIT_CONFIG_GLOBAL $beforeConfig
    Assert-Equal $env:PATH $beforePath
    Assert-Equal $env:PSModulePath $beforeModules
    Assert-FileChecksum $probeFile $probeHash
    $fixtureStorage.Validate()
    if ([IO.File]::ReadAllText($script:InstallLog) -match 'synthetic-fixture-token') {
        throw 'Child credential reached log.'
    }

    # Atomic creation/owner DACL is a native check, not a POSIX chmod assertion.
    $privateStorage = New-Object UnifiedBootstrap.InstallStorage($root)
    $private = $privateStorage.Root
    try {
        $acl = Get-Acl -LiteralPath $private
        Assert-Equal $acl.AreAccessRulesProtected $true
        $owner = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        Assert-Equal ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value) $owner
        foreach ($rule in $acl.Access) {
            $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
            if ($sid -notin @($owner, 'S-1-5-18')) { throw 'Unexpected private-directory trustee.' }
        }
    } finally {
        $privateStorage.Dispose()
        Remove-Item -LiteralPath $private -Recurse -Force
    }

    # Execute the REAL bounded downloader with only its request factory replaced.
    # Synthetic responses have no socket, listener, proxy, credentials or helper.
    # Keep this before the orchestration-specific Get-BoundedDownload stubs below.
    $requestFactory = ${function:New-InstallDownloadRequest}
    $script:OfflineBytes = [Text.Encoding]::UTF8.GetBytes(('stream' * 12000) + [char]0x03B2)
    $script:OfflineLength = $script:OfflineBytes.Length
    $script:OfflineRequests = 0
    $script:OfflineStreams = New-Object 'Collections.Generic.List[IO.MemoryStream]'
    $script:OfflineResponses = New-Object 'Collections.Generic.List[object]'
    function New-InstallDownloadRequest([Uri] $Uri) {
        Assert-Equal $Uri.AbsoluteUri 'https://github.com/offline-fixture'
        $script:OfflineRequests++
        $response = [pscustomobject]@{
            StatusCode = 200; ContentLength = $script:OfflineLength
            Bytes = $script:OfflineBytes; Disposed = $false
        }
        $response | Add-Member ScriptMethod GetResponseStream {
            $bytes = $this.Bytes
            $stream = New-Object IO.MemoryStream(,$bytes)
            $script:OfflineStreams.Add($stream)
            return $stream
        }
        $response | Add-Member ScriptMethod Dispose { $this.Disposed = $true }
        $script:OfflineResponses.Add($response)
        $request = [pscustomobject]@{
            Response = $response; AllowAutoRedirect = $true
            Timeout = 0; ReadWriteTimeout = 0; UserAgent = ''; UseDefaultCredentials = $true
        }
        $request | Add-Member ScriptMethod GetResponse { return $this.Response }
        return $request
    }
    try {
        $download = Join-Path $root 'offline.bin'
        Get-BoundedDownload 'https://github.com/offline-fixture' $download 1000000
        Assert-Equal $script:OfflineRequests 1
        Assert-PrivateFileAcl $download
        Assert-Equal ([IO.File]::ReadAllText($download)) (('stream' * 12000) + [char]0x03B2)
        Assert-Equal ([IO.File]::Exists($download + '.part')) $false
        foreach ($failure in @('oversize', 'incomplete', 'part-collision', 'destination-collision')) {
            $destination = Join-Path $root ($failure + '.bin')
            $temporary = $destination + '.part'
            $script:OfflineLength = $script:OfflineBytes.Length
            $limit = 1000000
            if ($failure -eq 'oversize') {
                # One complete 65536-byte write precedes the bound violation.
                $script:OfflineLength = -1
                $limit = 65536
            } elseif ($failure -eq 'incomplete') {
                $script:OfflineLength++
            } elseif ($failure -eq 'part-collision') {
                Write-PrivateInstallText $temporary 'part winner unchanged'
            } else {
                Write-PrivateInstallText $destination 'destination winner unchanged'
            }
            $script:OfflineRequests = 0
            Assert-Refused {
                Get-BoundedDownload 'https://github.com/offline-fixture' $destination $limit
            } 'Download failed twice'
            Assert-Equal $script:OfflineRequests 2
            if ($failure -eq 'part-collision') {
                Assert-Equal ([IO.File]::ReadAllText($temporary)) 'part winner unchanged'
            } else {
                Assert-Equal ([IO.File]::Exists($temporary)) $false
            }
            if ($failure -eq 'destination-collision') {
                Assert-Equal ([IO.File]::ReadAllText($destination)) 'destination winner unchanged'
            } else {
                Assert-Equal ([IO.File]::Exists($destination)) $false
            }
        }
        foreach ($inputStream in $script:OfflineStreams) { Assert-Equal $inputStream.CanRead $false }
        foreach ($response in $script:OfflineResponses) { Assert-Equal $response.Disposed $true }
        $fixtureStorage.Validate()
    } finally { ${function:New-InstallDownloadRequest} = $requestFactory }

    # Execute actual orchestration with fixture downloads, WITHOUT helpers,
    # app installation, service registration, browser access or network.
    function Assert-NativePlatform {}
    function New-PrivateInstallDirectory {
        $script:InstallStorage = New-Object UnifiedBootstrap.InstallStorage($root)
        return $script:InstallStorage.Root
    }
    function Find-InstallHelper { throw 'FIXTURE: helper must not run' }
    $script:Downloads = New-Object 'Collections.Generic.List[string]'
    function Get-BoundedDownload([string] $Url, [string] $Destination, [long] $MaxBytes) {
        $script:Downloads.Add($Url)
        Write-InstallReceipt $Destination @{
            draft = $false; prerelease = $false; tag_name = 'v0.44.0'
            assets = @(@{name = 'SHA256SUMS'}, @{name = 'amplifier_unified-0.44.0-py3-none-any.whl'})
        }
    }
    Assert-Refused { Invoke-UnifiedInstall 'latest' $true $true } 'Release v0.44.0 has no native Windows support record'
    Assert-Equal $script:Downloads.Count 1
    Assert-Equal $script:Downloads[0] 'https://api.github.com/repos/microsoft/amplifier-unified/releases/latest'
    $script:Downloads.Clear()
    Assert-Refused { Invoke-UnifiedInstall '0.44.0' $true $false } 'Release v0.44.0 has no native Windows support record'
    Assert-Equal $script:Downloads[0] 'https://api.github.com/repos/microsoft/amplifier-unified/releases/tags/v0.44.0'

    # A publisher record alone cannot open the unintegrated activation seam.
    # All content is synthetic; hash validation and orchestration are real.
    $script:FixtureBodies = @{}
    $script:FixtureBodies['native-windows-support.json'] = ConvertTo-Json $support -Depth 8
    $script:FixtureBodies[$name] = 'synthetic wheel'
    $script:FixtureBodies['windows-host-constraints.txt'] = 'amplifier-core==2.0.2'
    $script:FixtureBodies['windows-default-graph.json'] = '{"fixture":true}'
    $lines = @()
    foreach ($asset in $script:FixtureBodies.Keys) {
        $sha = [Security.Cryptography.SHA256]::Create()
        try {
            $bytes = [Text.Encoding]::UTF8.GetBytes($script:FixtureBodies[$asset])
            $digest = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
            $lines += "$digest  $asset"
        } finally { $sha.Dispose() }
    }
    $script:FixtureBodies['SHA256SUMS'] = $lines -join "`n"
    $script:BadWheel = $false
    function Get-BoundedDownload([string] $Url, [string] $Destination, [long] $MaxBytes) {
        $script:Downloads.Add($Url)
        if ($Url -match '/releases/(latest|tags/v0\.44\.0)$') {
            Write-InstallReceipt $Destination @{
                draft = $false; prerelease = $false; tag_name = 'v0.44.0'
                assets = @(@{name = 'native-windows-support.json'})
            }
        } else {
            $asset = ([Uri]$Url).Segments[-1]
            $body = $script:FixtureBodies[$asset]
            if ($script:BadWheel -and $asset -eq $name) { $body = 'corrupted fixture' }
            Write-PrivateInstallText $Destination $body
        }
    }
    $script:Downloads.Clear()
    Assert-Refused { Invoke-UnifiedInstall 'latest' $true $true } 'Native activation integration is pending'
    Assert-Equal $script:Downloads.Count 6
    $script:BadWheel = $true
    Assert-Refused { Invoke-UnifiedInstall '0.44.0' $true $true } 'Artifact checksum mismatch'
    # The positive control belongs after ALL fixture pins have been released,
    # not just $pinned: the outer fixture owns traversal/descendant pins too.
    # Keep both renames mandatory; a failure is not retried or treated as a pass.
    $fixtureStorage.Dispose()
    # Retain the original File.Move positive control, now with ALL pins closed.
    # This must succeed immediately on the exact privately created payload.
    [IO.File]::Move($published, $part)
    [IO.File]::Move($part, $published)
    Assert-PrivateFileAcl $published
    Assert-Equal ([IO.File]::ReadAllText($published)) 'private stream'
    [IO.Directory]::Move($ancestor, ($ancestor + '-replacement'))
    [IO.Directory]::Move(($ancestor + '-replacement'), $ancestor)
    Assert-Equal ([IO.File]::ReadAllText($canary)) 'outside remains unchanged'
    Write-Output 'OFFLINE FIXTURE PASS (not clean-machine or native installation qualification)'
} catch {
    # Offline synthetic fixture only: report the original source location
    # before cleanup can obscure it. Do not dump environment or child output.
    Write-Output 'OFFLINE FIXTURE FAILURE LOCATION'
    Write-Output $_.InvocationInfo.PositionMessage
    Write-Output $_.ScriptStackTrace
    throw
} finally {
    $fixtureStorage.Dispose()
    Remove-Item -LiteralPath $root -Recurse -Force
}