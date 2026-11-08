#requires -Version 5.1
# Offline command-model fixture. Never calls network, Python, uv, Git or app.
param([Parameter(Mandatory=$true)][string] $Installer)
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$null = [Management.Automation.Language.Parser]::ParseFile($Installer, [ref]$tokens, [ref]$parseErrors)
if (@($parseErrors).Count -ne 0) { throw 'Installer has PowerShell grammar errors.' }
. $Installer

function Need($Value, [string] $Message) {
    if (-not $Value) { throw "Prototype fixture: $Message" }
}
# Real, offline Windows rename regression: uv's publication pattern must work
# inside tool-write directories without losing no-DELETE sharing or private ACLs.
$nativeRoot = New-PrivateInstallDirectory
$nativeStorage = $script:InstallStorage
try {
    $pythonRoot = Join-Path $nativeRoot 'python'
    $nativeStorage.CreateDirectory($pythonRoot)
    $nativeStorage.EnableToolWrites($nativeRoot)
    $staged = Join-Path $nativeRoot 'staged-interpreter'
    [IO.Directory]::CreateDirectory($staged) | Out-Null
    $destination = Join-Path $pythonRoot 'cpython-fixture'
    $refused=$false
    try { [IO.Directory]::Move($staged,$destination) } catch { $refused=$true }
    Need $refused 'strict destination pin unexpectedly allowed absolute-target rename'
    $nativeStorage.EnableToolWrites($pythonRoot)
    [IO.Directory]::Move($staged,$destination)
    Need ([IO.Directory]::Exists($destination)) 'tool publication rename did not succeed'
    $nativeStorage.Validate()
    $acl = Get-Acl -LiteralPath $pythonRoot
    Need $acl.AreAccessRulesProtected 'tool mutation changed protected ACL'
    Need ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ceq
        [Security.Principal.WindowsIdentity]::GetCurrent().User.Value) 'tool mutation changed owner'
    $refused=$false
    try { [IO.Directory]::Move($pythonRoot,($pythonRoot + '-renamed')) } catch { $refused=$true }
    Need $refused 'tool mutation lost no-DELETE sharing'
    $refused=$false
    try { $nativeStorage.EnableToolWrites([IO.Path]::GetDirectoryName($nativeRoot)) } catch { $refused=$true }
    Need $refused 'ancestor was allowed tool mutation'
} finally {
    $nativeStorage.Dispose()
    [IO.Directory]::Delete($nativeRoot,$true)
}
$base = Join-Path ([IO.Path]::GetTempPath()) ('prototype-fixture-' + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($base) | Out-Null
try {
    # These are explicit model seams. ACL/download/archive execution remains in
    # the existing native install.Tests.ps1 fixture, not simulated qualification.
    function Assert-NativePlatform { }
    function New-PrivateInstallDirectory {
        $root = Join-Path $base ([guid]::NewGuid().ToString('N'))
        [IO.Directory]::CreateDirectory($root) | Out-Null
        $script:InstallStorage = New-Object PSObject
        $script:InstallStorage | Add-Member ScriptMethod HoldFile { param($path,$mutable) }
        $script:InstallStorage | Add-Member ScriptMethod EnsureDirectory { param($path) }
        $script:InstallStorage | Add-Member ScriptMethod EnableToolWrites { param($path) }
        $script:InstallStorage | Add-Member ScriptMethod Dispose { }
        return $root
    }
    function Write-PrivateInstallText { param($Path,$Text,[switch]$Append,[switch]$Mutable) }
    function Write-InstallReceipt { param($Path,$Value) $script:Receipts += $Value }
    function Get-BoundedDownload {
        param($Url,$Destination,$MaxBytes)
        $script:Downloads += $Url
        if ($Destination.EndsWith('prototype-plan.json')) {
            [IO.File]::WriteAllText($Destination, ($script:Plan | ConvertTo-Json -Depth 5))
        }
    }
    function Assert-FileChecksum {
        param($Path,$Expected)
        if ($script:BadHash) { throw 'Artifact checksum mismatch' }
    }
    function Expand-ContainedZip {
        param($Archive,$Destination)
        [IO.Directory]::CreateDirectory($Destination) | Out-Null
        [IO.File]::WriteAllText((Join-Path $Destination 'capsule.json'),
            (@{source=$script:Plan.source;version=$script:Plan.version;
                sourceMode=$(if ($script:Plan.ContainsKey('sourceMode')) {$script:Plan.sourceMode} else {'offline-wheels'})} | ConvertTo-Json))
    }
    function Find-InstallHelper {
        param($Name,$Environment)
        if ($script:UseExisting) { return (Join-Path $base ('existing-' + $Name + '.exe')) }
        return $null
    }
    function Install-VerifiedHelper {
        param($Name)
        $script:Helpers += $Name
        return (Join-Path $script:InstallWork ($Name + '.exe'))
    }
    function Invoke-InstallChild {
        param($Executable,$Arguments,$Environment)
        $script:Children += ,@($Executable,$Arguments,$Environment)
        if ($Arguments.Count -eq 1 -and $Arguments[0] -eq '--version') { return 'uv 0.12.18' }
        if ($Arguments[0] -eq 'python' -and $Arguments[1] -eq 'find') {
            return (Join-Path $script:InstallWork 'python\cpython-3.13.8\python.exe')
        }
        if ($script:FailInstall -and $Arguments[0] -eq 'pip' -and $Arguments[1] -eq 'install') {
            throw 'model install failure'
        }
        return ''
    }
    function Write-PrototypeShortcut {
        param($Python,$Launcher)
        $script:Links += ,@($Python,$Launcher)
        return (Join-Path $script:InstallWork 'Launch Unified.lnk')
    }
    function Reset-Model {
        $script:Plan = @{schemaVersion=1;status='prototype';source=('a'*40);version='0.54.5';
            python='3.13.8';architecture='x86_64';capsule=@{
                url='https://github.com/microsoft/amplifier-unified/releases/download/prototype-abc/windows-prototype.zip';
                sha256=('b'*64)}}
        $script:Downloads=@();$script:Helpers=@();$script:Children=@();$script:Receipts=@();$script:Links=@()
        $script:BadHash=$false;$script:FailInstall=$false
        $script:UseExisting=$false
    }
    Reset-Model
    $beforePath=$env:PATH
    Invoke-UnifiedPrototype 'https://github.com/microsoft/amplifier-unified/releases/download/prototype-abc/plan.json' ('c'*64) $true
    Need ($script:Helpers.Count -eq 2) 'missing uv/Git must use verified helper acquisition'
    Need ($script:Links.Count -eq 1) 'complete install must create one absolute relaunch link'
    Need ($script:Receipts.Count -eq 1) 'one completed candidate receipt expected'
    Need ($env:PATH -ceq $beforePath) 'parent PATH changed'
    $installs=@($script:Children | Where-Object { $_[1][0] -eq 'pip' -and $_[1][1] -eq 'install' })
    Need ($installs.Count -eq 1) 'package mutation must occur once'
    foreach ($flag in @('--require-hashes','--no-index','--no-deps','--only-binary')) {
        Need ($installs[0][1] -contains $flag) "missing offline immutable install flag $flag"
    }
    Need ($installs[0][2].AMPLIFIER_HOME -ne $installs[0][2].AMPLIFIER_SESSION_STATE_HOME) 'history and ownership merged'
    Need (-not (($script:Children | ForEach-Object { $_[1] }) -contains 'service')) 'service command emitted'

    Reset-Model
    $script:UseExisting=$true
    Invoke-UnifiedPrototype 'https://github.com/microsoft/amplifier-unified/releases/download/prototype-abc/plan.json' ('c'*64) $true
    Need ($script:Helpers.Count -eq 0) 'compatible tools were not reused'
    Need ($script:Links.Count -eq 1) 'reusable-tool path did not prepare relaunch'

    Reset-Model
    $script:Plan.source='1c4ac3b4f9ed5d2d3f588f6fa6c74a119e439153'
    $script:Plan.sourceMode='pinned-vcs'
    $script:UseExisting=$true
    Invoke-UnifiedPrototype 'https://github.com/microsoft/amplifier-unified/releases/download/prototype-abc/plan.json' ('c'*64) $true
    Need ($script:Helpers.Count -eq 2) 'source mode must use pinned private tools even with developer tools present'
    $installs=@($script:Children | Where-Object { $_[1][0] -eq 'pip' -and $_[1][1] -eq 'install' })
    Need ($installs.Count -eq 2) 'source mode must separate hashed wheels from exact Git'
    Need ($installs[0][1] -contains '--require-hashes') 'registry/app phase lost hash enforcement'
    Need (-not ($installs[1][1] -contains '--require-hashes')) 'Git phase must not claim wheel hashes for source'
    foreach ($flag in @('--no-deps','--no-index','--no-require-hashes','--verify-hashes',
        '--only-binary','--no-binary','--build-constraint','--find-links')) {
        Need ($installs[1][1] -contains $flag) "source phase missing $flag"
    }
    $validations=@($script:Children | Where-Object { $_[1] -contains '--validate-source' })
    $audits=@($script:Children | Where-Object { $_[1] -contains '--audit-source' })
    Need ($validations.Count -eq 1 -and $audits.Count -eq 1) 'source payload and installed origins both require validation'
    Need ($script:Receipts[0].bootstrapUv -ceq '0.12.18') 'bootstrap uv phase identity missing'
    Need ($script:Receipts[0].sourceMode -ceq 'pinned-vcs') 'prepared receipt source mode missing'
    Need ($env:PATH -ceq $beforePath) 'source mode changed parent PATH'
    $inherited = @{}
    foreach ($name in @('USERPROFILE','HOMEDRIVE','HOMEPATH')) {
        $inherited[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    $modelEnvironment = Get-PrototypeEnvironment $script:InstallWork 'C:\private tools\uv.exe' 'C:\private git\cmd\git.exe'
    foreach ($name in $inherited.Keys) {
        Need (-not $modelEnvironment.ContainsKey($name)) "standard profile override emitted for $name"
        Need ([Environment]::GetEnvironmentVariable($name, 'Process') -ceq $inherited[$name]) "parent profile changed for $name"
    }
    Need ($modelEnvironment.UV_TOOL_DIR -ceq (Join-Path $script:InstallWork 'tools')) 'private tool root missing'
    Need ($modelEnvironment.UV_TOOL_BIN_DIR -ceq (Join-Path $script:InstallWork 'tool-bin')) 'private tool bin root missing'

    Reset-Model
    $script:Plan.sourceMode='pinned-vcs'
    $failed=$false
    try { Invoke-UnifiedPrototype 'https://github.com/microsoft/amplifier-unified/releases/download/prototype-abc/plan.json' ('c'*64) $true }
    catch { $failed=$true }
    Need $failed 'source mode accepted an app commit outside the frozen plan'
    Need ($script:Helpers.Count -eq 0 -and $script:Children.Count -eq 0) 'source identity refusal executed helpers'

    Reset-Model
    $script:BadHash=$true
    $failed=$false
    try { Invoke-UnifiedPrototype 'https://github.com/microsoft/amplifier-unified/releases/download/prototype-abc/plan.json' ('c'*64) $true }
    catch { $failed=$true }
    Need $failed 'bad checksum accepted'
    Need ($script:Helpers.Count -eq 0 -and $script:Children.Count -eq 0) 'hash failure executed a helper'

    Reset-Model
    $script:Plan.status='qualified'
    $failed=$false
    try { Invoke-UnifiedPrototype 'https://github.com/microsoft/amplifier-unified/releases/download/prototype-abc/plan.json' ('c'*64) $true }
    catch { $failed=$true }
    Need $failed 'prototype accepted a forged public support claim'
    Need ($script:Helpers.Count -eq 0) 'invalid manifest changed helper state'

    Reset-Model
    $script:FailInstall=$true
    $failed=$false
    try { Invoke-UnifiedPrototype 'https://github.com/microsoft/amplifier-unified/releases/download/prototype-abc/plan.json' ('c'*64) $true }
    catch { $failed=$true }
    Need $failed 'partial installation did not fail'
    Need ($script:Links.Count -eq 0) 'partial install exposed a launcher'
    Need ($script:Receipts.Count -eq 0) 'partial install reported prepared receipt'
    Need ($env:PATH -ceq $beforePath) 'failure changed parent PATH'
    Write-Output 'PROTOTYPE OFFLINE MODEL PASS (not native installation acceptance)'
} finally {
    [IO.Directory]::Delete($base,$true)
}