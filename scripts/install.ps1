#requires -Version 5.1
<#
.SYNOPSIS
Review-first native Windows bootstrap candidate. Not yet qualified for use.
.DESCRIPTION
Save and inspect this file before running it as the signed-in, non-elevated
owner. Do not change execution policy to run it. Parameters mirror install.sh:
-Version, -NoOpen, -Anonymous and the built-in -Verbose.
The separate -PrototypeManifestUrl/-PrototypeManifestSha256 entry prepares an
explicit unqualified foreground candidate; it does not open the public gate.

Release contract (proposed, owned here until release integration):
native-windows-support.json must be an asset of the EXACT selected stable
release and have exactly one entry in that release's SHA256SUMS. Schema:
{
  "schemaVersion": 1, "release": "vX.Y.Z", "status": "qualified",
  "os": "windows-11", "architecture": "x86_64", "python": "cpython-3.13",
  "powershell": ["5.1", "7"],
  "nativePackages": ["amplifier-core", "...complete qualified native graph..."],
  "hostConstraints": "windows-host-constraints.txt",
  "defaultGraph": "windows-default-graph.json"
}
Both graph assets must also have unique SHA256SUMS entries. Constraints apply
only to this installation, not permanent component pins. defaultGraph is
retained qualification evidence, NOT proof of the installed worker graph.
Checksums obtained from the publisher are integrity, not independent signatures.

The lifecycle/default-worker qualification handoff is deliberately CLOSED in
Assert-NativeActivationContract. Current CLI has service install/status,
doctor and open, but no credential-free default-runtime prepare/inspect command.
Do not remove that gate just because a release publishes a support JSON file.
The lifecycle owner must integrate exact graph preparation/inspection, retained
prior generation and authenticated readiness before enabling package mutation.
v0.44.0 has no support record and is refused before any helper installation.
#>
[CmdletBinding()]
param(
    [ValidatePattern('^(latest|v?[0-9]+\.[0-9]+\.[0-9]+)$')]
    [string] $Version = 'latest',
    [switch] $NoOpen,
    [switch] $Anonymous,
    # Explicit experimental entry. This never manufactures a public support record.
    [string] $PrototypeManifestUrl,
    [ValidatePattern('^([0-9a-f]{64})?$')]
    [string] $PrototypeManifestSha256
)

function Assert-NativePlatform {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'Use native Windows 11 x64 PowerShell, not WSL or a POSIX shell.'
    }
    $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
    $architectures = @(Get-CimInstance -ClassName Win32_Processor -ErrorAction Stop |
        Select-Object -ExpandProperty Architecture -Unique)
    if ([int]$os.BuildNumber -lt 22000 -or $os.ProductType -ne 1 -or
        $architectures.Count -ne 1 -or $architectures[0] -ne 9 -or
        -not [Environment]::Is64BitProcess) {
        throw 'This candidate requires Windows 11 x64 and a 64-bit PowerShell process. ARM64 and Windows Server are not qualified.'
    }
    if (-not (($PSVersionTable.PSVersion.Major -eq 5 -and $PSVersionTable.PSVersion.Minor -ge 1) -or
        $PSVersionTable.PSVersion.Major -eq 7)) {
        throw 'Use Windows PowerShell 5.1 or PowerShell 7.'
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run setup as your normal signed-in user, not an administrator.'
    }
}

function Initialize-InstallStorage {
    # Narrow bootstrap lifetime, not an application filesystem abstraction.
    # Same-TokenUser processes and the named OS authorities are trusted. Foreign
    # mutation is rejected, not repaired. Read/traverse on ancestors is harmless.
    if (-not ('UnifiedBootstrap.InstallStorage' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.Win32.SafeHandles;
namespace UnifiedBootstrap {
    public sealed class InstallStorage : IDisposable {
        sealed class Pin {
            public string path; public SafeFileHandle handle;
            public uint volume, high, low; public bool directory, privateAcl, mutable;
        }
        readonly Dictionary<string, Pin> pins =
            new Dictionary<string, Pin>(StringComparer.OrdinalIgnoreCase);
        readonly List<Pin> order = new List<Pin>();
        readonly string owner = WindowsIdentity.GetCurrent().User.Value;
        bool disposed;
        public string Root { get; private set; }
        [StructLayout(LayoutKind.Sequential)] struct Attributes {
            public int length; public IntPtr descriptor;
            [MarshalAs(UnmanagedType.Bool)] public bool inherit;
        }
        [StructLayout(LayoutKind.Sequential)] struct Info {
            public uint attributes; public System.Runtime.InteropServices.ComTypes.FILETIME creation, access, write;
            public uint volume, sizeHigh, sizeLow, links, high, low;
        }
        [StructLayout(LayoutKind.Sequential)] struct RenameInfo {
            public byte replace; public IntPtr root; public uint length; public ushort name;
        }
        [StructLayout(LayoutKind.Sequential)] struct IoStatus {
            public IntPtr status; public UIntPtr information;
        }
        [DllImport("advapi32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
        static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(
            string sddl, uint revision, out IntPtr descriptor, out uint size);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
        static extern bool CreateDirectoryW(string path, ref Attributes attributes);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
        static extern SafeFileHandle CreateFileW(string path, uint access, uint share,
            IntPtr security, uint disposition, uint flags, IntPtr template);
        [DllImport("kernel32.dll", EntryPoint="CreateFileW", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
        static extern SafeFileHandle CreatePrivateFileW(string path, uint access, uint share,
            ref Attributes security, uint disposition, uint flags, IntPtr template);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool GetFileInformationByHandle(SafeFileHandle handle, out Info info);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
        static extern uint GetFinalPathNameByHandleW(SafeFileHandle handle, StringBuilder path, uint length, uint flags);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, ExactSpelling=true)]
        static extern uint GetDriveTypeW(string root);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
        static extern bool GetVolumeInformationByHandleW(SafeFileHandle handle, StringBuilder name,
            uint nameSize, out uint serial, out uint maxComponent, out uint flags, StringBuilder fs, uint fsSize);
        [DllImport("advapi32.dll")]
        static extern uint GetSecurityInfo(SafeFileHandle handle, uint type, uint information,
            out IntPtr owner, out IntPtr group, out IntPtr dacl, out IntPtr sacl, out IntPtr descriptor);
        [DllImport("advapi32.dll")] static extern uint GetSecurityDescriptorLength(IntPtr descriptor);
        [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr value);
        [DllImport("ntdll.dll", ExactSpelling=true)]
        static extern int NtSetInformationFile(SafeFileHandle handle, out IoStatus status,
            IntPtr information, uint length, int informationClass);
        [DllImport("ntdll.dll", ExactSpelling=true)]
        static extern uint RtlNtStatusToDosError(int status);
        static void Need(bool ok) { if (!ok) throw new Win32Exception(Marshal.GetLastWin32Error()); }
        static string Address(string path) {
            // Validate BEFORE GetFullPath can remove dots or normalize aliases.
            if (path == null || !Regex.IsMatch(path, @"^[A-Za-z]:\\") || path.Length > 240)
                throw new IOException("Bootstrap storage requires bounded absolute local paths.");
            if (path.Length > 3) foreach (string part in path.Substring(3).Split('\\')) {
                if (part.Length == 0 || part == "." || part == ".." || part.EndsWith(".") || part.EndsWith(" ") ||
                    Regex.IsMatch(part, "[<>:\"/|?*~\\x00-\\x1f]") ||
                    Regex.IsMatch(part, @"^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])(\.|$)",
                        RegexOptions.IgnoreCase)) throw new IOException("Unsafe bootstrap path component.");
            }
            return Path.GetFullPath(path);
        }
        bool Trusted(string sid) {
            return sid == owner || sid == "S-1-5-18" || sid == "S-1-5-32-544" ||
                sid == "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464";
        }
        void CheckAcl(Pin pin) {
            IntPtr user, group, dacl, sacl, descriptor;
            uint error = GetSecurityInfo(pin.handle, 1, 5, out user, out group, out dacl, out sacl, out descriptor);
            if (error != 0) throw new Win32Exception((int)error);
            try {
                uint length = GetSecurityDescriptorLength(descriptor);
                if (length == 0 || length > 65536) throw new IOException("Unsupported bootstrap ACL size.");
                var bytes = new byte[(int)length]; Marshal.Copy(descriptor, bytes, 0, bytes.Length);
                var sd = new RawSecurityDescriptor(bytes, 0);
                if (sd.Owner == null || !Trusted(sd.Owner.Value) ||
                    (pin.privateAcl && sd.Owner.Value != owner) || sd.DiscretionaryAcl == null ||
                    (sd.ControlFlags & ControlFlags.DiscretionaryAclPresent) == 0)
                    throw new IOException("Untrusted bootstrap owner or null DACL.");
                foreach (GenericAce raw in sd.DiscretionaryAcl) {
                    var ace = raw as CommonAce;
                    if (ace == null || ace.IsCallback || ((int)ace.AceFlags & ~0x1f) != 0 ||
                        (ace.AceQualifier != AceQualifier.AccessAllowed && ace.AceQualifier != AceQualifier.AccessDenied))
                        throw new IOException("Unsupported bootstrap ACE.");
                    uint mask = unchecked((uint)ace.AccessMask);
                    if ((mask & ~0xF01F01FFu) != 0) throw new IOException("Unsupported bootstrap access mask.");
                    if (ace.AceQualifier == AceQualifier.AccessDenied) continue;
                    if (Trusted(ace.SecurityIdentifier.Value) || ace.SecurityIdentifier.Value == "S-1-3-4") continue;
                    if (!pin.privateAcl && pin.directory && (ace.AceFlags & AceFlags.InheritOnly) != 0) continue;
                    // Data/EA/attributes/delete-child/delete/DACL/owner plus generic
                    // WRITE/ALL are mutation. ADD_SUBDIRECTORY alone is harmless
                    // on traversal ancestors; no child is adopted in private storage.
                    const uint Mutation = 0x500D0156u;
                    uint mutation = pin.directory ? Mutation & ~4u : Mutation; // files: APPEND_DATA is mutation
                    if (pin.privateAcl ? mask != 0 : (mask & mutation) != 0)
                        throw new IOException("Foreign mutation/access in bootstrap traversal ACL.");
                }
            } finally { LocalFree(descriptor); }
        }
        Info Inspect(Pin pin) {
            Info info; Need(GetFileInformationByHandle(pin.handle, out info));
            if ((info.attributes & 0x400) != 0 || ((info.attributes & 0x10) != 0) != pin.directory ||
                (!pin.directory && info.links != 1)) throw new IOException("Bootstrap reparse/type/hardlink refusal.");
            var final = new StringBuilder(32768);
            uint count = GetFinalPathNameByHandleW(pin.handle, final, (uint)final.Capacity, 0);
            if (count == 0 || count >= final.Capacity ||
                !String.Equals(final.ToString(), @"\\?\" + pin.path, StringComparison.OrdinalIgnoreCase))
                throw new IOException("Bootstrap namespace identity changed.");
            CheckAcl(pin);
            return info;
        }
        void Verify(Pin pin) {
            Info info = Inspect(pin);
            if (info.volume != pin.volume || info.high != pin.high || info.low != pin.low)
                throw new IOException("Bootstrap file identity changed.");
        }
        Pin Hold(string path, bool directory, bool privateAcl, bool mutable) {
            Pin old;
            if (pins.TryGetValue(path, out old)) { Verify(old); return old; }
            if (order.Count >= 4096) throw new IOException("Bootstrap pin budget exceeded.");
            // OPEN_REPARSE_POINT + BACKUP_SEMANTICS. Deny DELETE sharing for
            // the entire lifetime, and deny WRITE sharing except our mutable log.
            // Metadata-only directory opens do not participate in NT sharing
            // checks. FILE_LIST_DIRECTORY (1) makes the no-DELETE share effective.
            var pin = new Pin { path=path, directory=directory, privateAcl=privateAcl, mutable=mutable };
            pin.handle = CreateFileW(path, 0x20080u | (directory ? 1u : 0x80000000u),
                mutable ? 3u : 1u, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
            try {
                if (pin.handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
                Info info = Inspect(pin);
                uint serial, max, flags; var fs = new StringBuilder(32);
                Need(GetVolumeInformationByHandleW(pin.handle, null, 0, out serial, out max, out flags, fs, 32));
                if (GetDriveTypeW(Path.GetPathRoot(path)) != 3 || fs.ToString() != "NTFS")
                    throw new IOException("Bootstrap storage requires fixed local NTFS.");
                Pin drive;
                if (pins.TryGetValue(Path.GetPathRoot(path), out drive) && info.volume != drive.volume)
                    throw new IOException("Bootstrap path crossed a volume boundary.");
                pin.volume=info.volume; pin.high=info.high; pin.low=info.low;
                pins.Add(path, pin); order.Add(pin); return pin;
            } catch { pin.handle.Dispose(); throw; }
        }
        void Traverse(string path) {
            path = Address(path);
            string cursor = Path.GetPathRoot(path);
            Hold(cursor, true, false, false);
            if (path.Length > 3) foreach (string part in path.Substring(3).Split('\\')) {
                cursor = Path.Combine(cursor, part);
                Hold(cursor, true, Root != null && IsOwnedDirectory(cursor), false);
            }
        }
        bool IsOwnedDirectory(string path) {
            return String.Equals(path, Root, StringComparison.OrdinalIgnoreCase) ||
                path.StartsWith(Root + "\\", StringComparison.OrdinalIgnoreCase);
        }
        void CreateExact(string path) {
            // Parent was pinned/validated before this call. No open-if-exists
            // or ACL repair. A collision is a refusal, even if its ACL looks safe.
            IntPtr descriptor; uint size;
            Need(ConvertStringSecurityDescriptorToSecurityDescriptorW(
                "O:" + owner + "D:P(A;OICI;FA;;;" + owner + ")(A;OICI;FA;;;SY)",
                1, out descriptor, out size));
            try {
                var attributes = new Attributes {
                    length=Marshal.SizeOf(typeof(Attributes)), descriptor=descriptor, inherit=false
                };
                if (!CreateDirectoryW(path, ref attributes)) {
                    int error = Marshal.GetLastWin32Error();
                    if (error == 183) throw new IOException("Extraction/setup destination already exists; never adopted.");
                    throw new Win32Exception(error);
                }
            } finally { LocalFree(descriptor); }
            Hold(path, true, true, false);
        }
        public InstallStorage(string parent) {
            try {
                parent = Address(parent); Traverse(parent);
                Root = Path.Combine(parent, "amplifier-install-" + Guid.NewGuid().ToString("N"));
                CreateExact(Root);
            } catch { Dispose(); throw; }
        }
        public void Validate() {
            if (disposed) throw new ObjectDisposedException("InstallStorage");
            foreach (Pin pin in order) Verify(pin);
        }
        public void AssertOwnedPath(string path) {
            if (disposed) throw new ObjectDisposedException("InstallStorage");
            path = Address(path);
            if (!path.StartsWith(Root + "\\", StringComparison.OrdinalIgnoreCase))
                throw new IOException("Write outside private bootstrap storage.");
            Pin parent;
            if (!pins.TryGetValue(Path.GetDirectoryName(path), out parent) || !parent.directory || !parent.privateAcl)
                throw new IOException("Bootstrap parent is not an owned pinned directory.");
            Verify(parent);
        }
        public void CreateDirectory(string path) {
            path = Address(path); AssertOwnedPath(path); CreateExact(path);
        }
        public void EnsureDirectory(string path) {
            path = Address(path);
            if (!IsOwnedDirectory(path)) throw new IOException("Extraction escaped private bootstrap storage.");
            Pin pin;
            if (pins.TryGetValue(path, out pin)) { Verify(pin); return; }
            EnsureDirectory(Path.GetDirectoryName(path));
            CreateDirectory(path); // Only this context's prior creations can be reused.
        }
        public void EnableToolWrites(string path) {
            // uv publishes by absolute-target rename: the destination directory
            // must share WRITE, but never DELETE. Only private directories this
            // exact bootstrap created may opt in; ancestors/files stay strict.
            path = Address(path);
            Pin pin;
            if (!pins.TryGetValue(path, out pin) || !pin.directory ||
                    !pin.privateAcl || !IsOwnedDirectory(path))
                throw new IOException("Tool mutation requires this bootstrap's private directory.");
            Verify(pin);
            SafeFileHandle replacement = CreateFileW(path, 0x20081u, 3u,
                IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
            try {
                if (replacement.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
                Info info = Inspect(new Pin {path=path, handle=replacement, directory=true, privateAcl=true});
                if (info.volume != pin.volume || info.high != pin.high || info.low != pin.low)
                    throw new IOException("Tool directory identity changed.");
                // The replacement is validated before the strict handle closes:
                // no unpinned interval and still no DELETE sharing.
                SafeFileHandle previous = pin.handle;
                pin.handle = replacement; replacement=null;
                previous.Dispose();
            } finally { if (replacement != null) replacement.Dispose(); }
        }
        public FileStream CreateFile(string path) {
            path = Address(path); AssertOwnedPath(path);
            if (order.Count >= 4096) throw new IOException("Bootstrap pin budget exceeded.");
            // Explicit TokenUser owner and protected owner/SYSTEM DACL at
            // CREATE_NEW, not inherited defaults or ACL repair after a write.
            IntPtr descriptor; uint size;
            Need(ConvertStringSecurityDescriptorToSecurityDescriptorW(
                "O:" + owner + "D:P(A;;FA;;;" + owner + ")(A;;FA;;;SY)",
                1, out descriptor, out size));
            try {
                var attributes = new Attributes {
                    length=Marshal.SizeOf(typeof(Attributes)), descriptor=descriptor, inherit=false
                };
                SafeFileHandle handle = CreatePrivateFileW(path, 0xC0020080u, 1u,
                    ref attributes, 1, 0x00200080, IntPtr.Zero);
                try {
                    if (handle.IsInvalid) {
                        int error = Marshal.GetLastWin32Error();
                        if (error == 80 || error == 183)
                            throw new IOException("Bootstrap file already exists; never adopted.");
                        throw new Win32Exception(error);
                    }
                    // Validate the very handle that will receive payload bytes.
                    Inspect(new Pin {path=path, handle=handle, privateAcl=true});
                    return new FileStream(handle, FileAccess.ReadWrite);
                } catch { handle.Dispose(); throw; }
            } finally { LocalFree(descriptor); }
        }
        public FileStream OpenAppend(string path) {
            path = Address(path); AssertOwnedPath(path);
            Pin pin;
            if (!pins.TryGetValue(path, out pin) || pin.directory || !pin.mutable)
                throw new IOException("Append requires this context's pinned mutable file.");
            Verify(pin);
            // OPEN_EXISTING only. Never create/adopt an unvalidated log on append.
            SafeFileHandle handle = CreateFileW(path, 0x40020080u, 3u,
                IntPtr.Zero, 3, 0x00200000, IntPtr.Zero);
            try {
                if (handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
                Info info = Inspect(new Pin {path=path, handle=handle, privateAcl=true});
                if (info.volume != pin.volume || info.high != pin.high || info.low != pin.low)
                    throw new IOException("Bootstrap append identity changed.");
                var stream = new FileStream(handle, FileAccess.Write);
                try { stream.Seek(0, SeekOrigin.End); return stream; }
                catch { stream.Dispose(); throw; }
            } catch { handle.Dispose(); throw; }
        }
        public void PublishFile(string source, string destination) {
            source = Address(source); destination = Address(destination);
            AssertOwnedPath(source); AssertOwnedPath(destination);
            if (!String.Equals(Path.GetDirectoryName(source), Path.GetDirectoryName(destination),
                    StringComparison.OrdinalIgnoreCase) ||
                String.Equals(source, destination, StringComparison.OrdinalIgnoreCase))
                throw new IOException("Bootstrap publication requires distinct names in the same directory.");
            if (pins.ContainsKey(source))
                throw new IOException("Cannot publish an already pinned bootstrap file.");
            // File.Move's absolute target opens its directory for FILE_WRITE_DATA,
            // conflicting with our no-WRITE-sharing directory pin. A native
            // FileRenameInformation with RootDirectory=NULL and a SIMPLE name
            // renames within the source directory without that target open.
            // https://learn.microsoft.com/windows-hardware/drivers/ddi/ntifs/ns-ntifs-_file_rename_information
            // Keep ALL pins/share masks unchanged; never replace or copy/delete.
            using (SafeFileHandle handle = CreateFileW(source, 0x80030080u, 1u,
                    IntPtr.Zero, 3, 0x00200000, IntPtr.Zero)) {
                if (handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
                Info before = Inspect(new Pin {path=source, handle=handle, privateAcl=true});
                byte[] name = Encoding.Unicode.GetBytes(Path.GetFileName(destination));
                int size = Marshal.SizeOf(typeof(RenameInfo)) + name.Length;
                IntPtr buffer = Marshal.AllocHGlobal(size);
                try {
                    // Zero replace and root, including native alignment padding.
                    Marshal.Copy(new byte[size], 0, buffer, size);
                    Marshal.WriteInt32(buffer, (int)Marshal.OffsetOf(typeof(RenameInfo), "length"), name.Length);
                    Marshal.Copy(name, 0, IntPtr.Add(buffer,
                        (int)Marshal.OffsetOf(typeof(RenameInfo), "name")), name.Length);
                    IoStatus io;
                    int status = NtSetInformationFile(handle, out io, buffer, (uint)size, 10);
                    if (status < 0) {
                        int error = (int)RtlNtStatusToDosError(status);
                        if (error == 80 || error == 183)
                            throw new IOException("Bootstrap publication destination already exists; never replaced.");
                        throw new Win32Exception(error);
                    }
                    try {
                        Info after = Inspect(new Pin {path=destination, handle=handle, privateAcl=true});
                        if (before.volume != after.volume || before.high != after.high || before.low != after.low)
                            throw new IOException("Bootstrap publication identity changed.");
                    } catch (Exception error) {
                        throw new IOException("Bootstrap publication completed but validation failed.", error);
                    }
                } finally { Marshal.FreeHGlobal(buffer); }
            }
        }
        public void HoldFile(string path, bool mutable) {
            path = Address(path); AssertOwnedPath(path); Hold(path, false, true, mutable);
        }
        public void TrustExecutable(string path) {
            path = Address(path); Traverse(Path.GetDirectoryName(path));
            Hold(path, false, IsOwnedDirectory(path), false);
        }
        public void Dispose() {
            if (disposed) return;
            for (int i=order.Count-1; i>=0; i--) order[i].handle.Dispose();
            pins.Clear(); order.Clear(); disposed=true;
        }
    }
}
'@
    }
}

function New-PrivateInstallDirectory {
    Initialize-InstallStorage
    $parent = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    $script:InstallStorage = New-Object UnifiedBootstrap.InstallStorage($parent)
    return $script:InstallStorage.Root
}

function Write-PrivateInstallText([string] $Path, [string] $Text, [switch] $Append, [switch] $Mutable) {
    $script:InstallStorage.AssertOwnedPath($Path)
    $stream = if ($Append) { $script:InstallStorage.OpenAppend($Path) } else { $script:InstallStorage.CreateFile($Path) }
    try {
        $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes($Text)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    } finally { $stream.Dispose() }
    # Append is reserved for the log, whose retained pin permits our writes.
    $script:InstallStorage.HoldFile($Path, ([bool]$Append -or [bool]$Mutable))
}

function Write-InstallReceipt([string] $Path, $Value) {
    $json = ConvertTo-Json -InputObject $Value -Depth 12
    Write-PrivateInstallText $Path $json
}

function New-InstallDownloadRequest([Uri] $Uri) {
    return [Net.HttpWebRequest]::Create($Uri)
}

function Get-BoundedDownload([string] $Url, [string] $Destination, [long] $MaxBytes) {
    # No credentials, HTTP downgrade, ambient auth, or unbounded redirect chain.
    # IWR performs the API preflight; .NET streams bodies because PS5 IWR
    # buffers them and -OutFile alone cannot enforce an actual byte limit.
    $allowedHosts = @('api.github.com', 'github.com', 'release-assets.githubusercontent.com',
        'objects.githubusercontent.com', 'releases.githubusercontent.com', 'raw.githubusercontent.com')
    $uri = [Uri]$Url
    $part = $Destination + '.part'
    $script:InstallStorage.Validate()
    $script:InstallStorage.AssertOwnedPath($Destination)
    $script:InstallStorage.AssertOwnedPath($part)
    $firstFailure = $null
    for ($attempt = 0; $attempt -lt 2; $attempt++) {
        $partCreated = $false
        try {
            $current = $uri
            if ($current.Scheme -ne 'https' -or $current.UserInfo -or
                $current.Port -ne 443 -or $allowedHosts -notcontains $current.DnsSafeHost) {
                throw 'Download refused an untrusted destination.'
            }
            if ($current.DnsSafeHost -eq 'api.github.com') {
                $null = Invoke-WebRequest -Uri $current.AbsoluteUri -Method Head -UseBasicParsing `
                    -Headers @{ 'User-Agent' = 'amplifier-unified-bootstrap' } `
                    -MaximumRedirection 0 -TimeoutSec 30 -ErrorAction Stop
            }
            for ($redirect = 0; $redirect -le 5; $redirect++) {
                if ($current.Scheme -ne 'https' -or $current.UserInfo -or
                    $current.Port -ne 443 -or $allowedHosts -notcontains $current.DnsSafeHost) {
                    throw 'Download refused an untrusted destination.'
                }
                $request = New-InstallDownloadRequest $current
                $request.AllowAutoRedirect = $false
                $request.Timeout = 30000
                $request.ReadWriteTimeout = 30000
                $request.UserAgent = 'amplifier-unified-bootstrap'
                $request.UseDefaultCredentials = $false
                $response = $request.GetResponse()
                if ([int]$response.StatusCode -in @(301, 302, 303, 307, 308)) {
                    try { $current = New-Object Uri($current, [string]$response.Headers['Location']) }
                    finally { $response.Dispose() }
                    continue
                }
                break
            }
            if ($redirect -gt 5) { throw 'Download redirect limit exceeded.' }
            try {
                if ([int]$response.StatusCode -ne 200) { throw 'Download HTTP failure.' }
                $length = $response.ContentLength
                if ($length -gt $MaxBytes) { throw 'Download exceeds its size bound.' }
                $inputStream = $response.GetResponseStream()
                $output = $null
                try {
                    $output = $script:InstallStorage.CreateFile($part)
                    $partCreated = $true
                    $buffer = New-Object byte[] 65536
                    $total = 0L
                    $clock = [Diagnostics.Stopwatch]::StartNew()
                    while (($count = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                        $total += $count
                        if ($total -gt $MaxBytes -or $clock.Elapsed.TotalSeconds -gt 180) {
                            throw 'Download exceeded its size or time budget.'
                        }
                        $output.Write($buffer, 0, $count)
                    }
                    if ($length -ge 0 -and $total -ne $length) { throw 'Download was incomplete.' }
                    $output.Flush($true)
                } finally {
                    if ($null -ne $output) { $output.Dispose() }
                    $inputStream.Dispose()
                }
            } finally { $response.Dispose() }
            $script:InstallStorage.PublishFile($part, $Destination)
            $partCreated = $false
            $script:InstallStorage.HoldFile($Destination, $false)
            return
        } catch {
            if ($null -eq $firstFailure) { $firstFailure = $_ }
            # A CREATE_NEW collision never authorizes deleting somebody else's file.
            if ($partCreated -and [IO.File]::Exists($part)) { [IO.File]::Delete($part) }
            # Retry downloads only. No installation or lifecycle mutation retry.
        }
    }
    # Do not echo exception text: a redirect/proxy error can carry credentials.
    throw ('Download failed twice; first failure type: ' + $firstFailure.Exception.GetType().Name +
        '. Check your HTTPS proxy/network policy, then download again.')
}

function Get-ExactChecksum([string] $Text, [string] $Name) {
    $matchesForName = @()
    foreach ($line in ($Text -split "`r?`n")) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        if ($line -cnotmatch '^([0-9a-fA-F]{64}) [ *]([^/\\\s]+)$') {
            throw 'Malformed SHA256SUMS; no artifact was executed.'
        }
        if ($Matches[2] -ceq $Name) { $matchesForName += $Matches[1].ToLowerInvariant() }
    }
    if ($matchesForName.Count -ne 1) { throw 'Missing or duplicate checksum entry.' }
    return $matchesForName[0]
}

function Assert-FileChecksum([string] $Path, [string] $Expected) {
    # Retain a read-only/no-delete-sharing pin from hash through later use.
    $script:InstallStorage.HoldFile($Path, $false)
    if ($Expected -cnotmatch '^[0-9a-f]{64}$' -or
        (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Expected) {
        throw 'Artifact checksum mismatch; no artifact was executed.'
    }
}

function Expand-ContainedZip([string] $Archive, [string] $Destination) {
    Add-Type -AssemblyName System.IO.Compression -ErrorAction Stop
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $script:InstallStorage.Validate()
    $script:InstallStorage.HoldFile($Archive, $false)
    $script:InstallStorage.CreateDirectory($Destination)
    $root = [IO.Path]::GetFullPath($Destination).TrimEnd('\') + '\'
    $seen = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        if ($zip.Entries.Count -gt 20000) { throw 'Archive has too many entries.' }
        $expanded = 0L
        # Validate the WHOLE archive before writing any entry.
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName.Replace('/', '\')
            $unixType = ($entry.ExternalAttributes -shr 16) -band 61440
            if ($name -match '(^\\|:|(^|\\)\.\.?($|\\)|[<>|"*?~\x00-\x1f])' -or
                $name -match '(^|\\)(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])(\.|\\|$)' -or
                $name -match '[. ](\\|$)' -or $unixType -eq 40960 -or
                ($entry.ExternalAttributes -band 1024)) { throw 'Unsafe ZIP entry.' }
            if (@($name.TrimEnd('\').Split('\') | Where-Object { $_ -eq '' }).Count -gt 0) {
                throw 'Unsafe ZIP empty component.'
            }
            $target = [IO.Path]::GetFullPath((Join-Path $Destination $name)).TrimEnd('\')
            if (-not $target.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) -or
                $target.Length -gt 240 -or -not $seen.Add($target)) { throw 'ZIP path escaped or duplicated its destination.' }
            $expanded += $entry.Length
            if ($expanded -gt 536870912) { throw 'Expanded archive exceeds 512 MiB.' }
        }
        foreach ($entry in $zip.Entries) {
            $target = Join-Path $Destination $entry.FullName.Replace('/', '\')
            if ($entry.FullName.EndsWith('/') -or $entry.FullName.EndsWith('\')) {
                $script:InstallStorage.EnsureDirectory($target.TrimEnd('\'))
            } else {
                $script:InstallStorage.EnsureDirectory([IO.Path]::GetDirectoryName($target))
                $script:InstallStorage.AssertOwnedPath($target)
                $output = $script:InstallStorage.CreateFile($target)
                try {
                    $inputStream = $entry.Open()
                    try { $inputStream.CopyTo($output) } finally { $inputStream.Dispose() }
                    $output.Flush($true)
                } finally { $output.Dispose() }
                $script:InstallStorage.HoldFile($target, $false)
            }
        }
        $script:InstallStorage.Validate()
    } finally { $zip.Dispose() }
}

function Read-NativeSupport($Record, [string] $Release) {
    if ($Record.schemaVersion -ne 1 -or $Record.release -cne "v$Release" -or
        $Record.status -cne 'qualified' -or $Record.os -cne 'windows-11' -or
        $Record.architecture -cne 'x86_64' -or $Record.python -cne 'cpython-3.13' -or
        @($Record.powershell) -notcontains '5.1' -or @($Record.powershell) -notcontains '7') {
        throw 'This exact release is not qualified for native Windows 11 x64 / CPython 3.13 / PowerShell 5.1 and 7. Keep the current installation.'
    }
    $native = @($Record.nativePackages)
    if ($native.Count -lt 1 -or $native.Count -gt 256 -or $native -notcontains 'amplifier-core') {
        throw 'Release lacks its qualified native dependency inventory.'
    }
    foreach ($name in $native) {
        if ($name -cnotmatch '^[a-z0-9]+(-[a-z0-9]+)*$') { throw 'Invalid native package identity.' }
    }
    foreach ($asset in @($Record.hostConstraints, $Record.defaultGraph)) {
        if ($asset -cnotmatch '^windows-[a-z0-9-]+\.(txt|json)$') { throw 'Invalid qualified graph asset name.' }
    }
    return $Record
}

function Assert-NativeActivationContract {
    # This is not a user-overridable "force" switch or a release flag.
    # Parent/lifecycle owner must replace this seam, with native evidence.
    throw 'Native activation integration is pending: the lifecycle owner must supply default-runtime graph prepare/inspect and rollback readiness. No package or working installation was changed. Use the existing supported WSL setup meanwhile.'
}

function ConvertTo-NativeArgument([string] $Value) {
    # CommandLineToArgvW/CRT quoting, including spaces, quotes and trailing slash.
    return '"' + [regex]::Replace(
        [regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}

function Format-PowerShellLaunchCommand([string] $Executable) {
    # PowerShell source quoting is NOT CRT argv quoting. A literal single quote
    # inside a single-quoted PowerShell string is represented by two single quotes.
    return "& '" + $Executable.Replace("'", "''") + "'"
}

function Invoke-InstallChild([string] $Executable, [string[]] $Arguments, [hashtable] $Environment) {
    if (-not [IO.Path]::IsPathRooted($Executable)) { throw 'Child executable must be absolute.' }
    # Includes existing PATH candidates: a --version probe is execution too.
    $script:InstallStorage.TrustExecutable($Executable)
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $Executable
    $info.Arguments = (($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' ')
    $info.WorkingDirectory = $script:InstallWork
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $info.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
    # All environment changes belong to this child; no process/global auth edits.
    if ($script:UseAnonymous) {
        foreach ($key in @($info.EnvironmentVariables.Keys)) {
            if ($key -like 'GIT_CONFIG_*' -or $key -in @('GH_TOKEN', 'GITHUB_TOKEN',
                'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN', 'GIT_ASKPASS', 'SSH_ASKPASS')) {
                $info.EnvironmentVariables.Remove($key)
            }
        }
        $info.EnvironmentVariables['AMPLIFIER_GITHUB_ACCESS'] = 'anonymous'
        $info.EnvironmentVariables['GIT_CONFIG_GLOBAL'] = 'NUL'
        $info.EnvironmentVariables['GIT_CONFIG_SYSTEM'] = 'NUL'
        $info.EnvironmentVariables['GIT_TERMINAL_PROMPT'] = '0'
        $info.EnvironmentVariables['GIT_CONFIG_COUNT'] = '2'
        $info.EnvironmentVariables['GIT_CONFIG_KEY_0'] = 'credential.helper'
        $info.EnvironmentVariables['GIT_CONFIG_VALUE_0'] = ''
        $info.EnvironmentVariables['GIT_CONFIG_KEY_1'] = 'core.askPass'
        $info.EnvironmentVariables['GIT_CONFIG_VALUE_1'] = ''
    }
    if ($script:InstallStage -like 'prototype*' -or $Environment.ContainsKey('UV_TOOL_DIR')) {
        # Keep the ordinary Windows profile identity. Discard only package/tool
        # overrides that could redirect a frozen install into another graph/root.
        foreach ($key in @($info.EnvironmentVariables.Keys)) {
            if ($key -like 'UV_*' -or $key -like 'PIP_*' -or
                $key -like '*_API_KEY' -or $key -like '*_TOKEN' -or
                $key -in @('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'GIT_SSL_NO_VERIFY',
                    'GIT_EXEC_PATH', 'GIT_TEMPLATE_DIR',
                    'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AZURE_CLIENT_SECRET')) {
                $info.EnvironmentVariables.Remove($key)
            }
        }
    }
    foreach ($key in $Environment.Keys) { $info.EnvironmentVariables[$key] = $Environment[$key] }
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        $null = $process.Start()
        # Drain both pipes concurrently. Never echo arbitrary child output or
        # browser-grant URLs to the terminal or log, even under -Verbose.
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $output = $stdout.GetAwaiter().GetResult()
        $null = $stderr.GetAwaiter().GetResult()
        Write-PrivateInstallText $script:InstallLog "$($script:InstallStage): child exit $($process.ExitCode)`r`n" -Append
        if ($process.ExitCode -ne 0) {
            throw "Child failed with exit $($process.ExitCode). Review this stage before any manual retry; installation effects may be partial."
        }
        return $output.Trim()
    } finally { $process.Dispose() }
}

function Find-InstallHelper([string] $Name, [hashtable] $Environment) {
    $commands = @(Get-Command "$Name.exe" -CommandType Application -ErrorAction SilentlyContinue)
    $candidates = @($commands | ForEach-Object { $_.Source })
    if ($Name -eq 'uv') {
        $candidates += @((Join-Path $HOME '.local\bin\uv.exe'), (Join-Path $HOME '.cargo\bin\uv.exe'))
    }
    foreach ($path in $candidates) {
        if (-not [IO.File]::Exists($path)) { continue }
        try {
            $identity = Invoke-InstallChild $path @('--version') $Environment
            if (($Name -eq 'git' -and $identity -match '^git version \d+\.\d+') -or
                ($Name -eq 'uv' -and $identity -match '^uv (\d+\.\d+\.\d+)')) {
                if ($Name -eq 'uv') {
                    $help = Invoke-InstallChild $path @('tool', 'install', '--help') $Environment
                    $pythonHelp = Invoke-InstallChild $path @('python', 'install', '--help') $Environment
                    if ($help -notmatch '--no-build-package' -or $help -notmatch '--managed-python' -or
                        $pythonHelp -notmatch '--no-bin' -or $pythonHelp -notmatch '--no-registry') { continue }
                }
                return $path
            }
        } catch { Write-Verbose "$Name candidate is unusable; checking another helper." }
    }
    return $null
}

function Install-VerifiedHelper([string] $Name) {
    # Official release metadata inspected 2026-10-31. SHA-256 values are pinned
    # GitHub asset digests, not guessed URLs or claims of signature verification.
    $helpers = @{
        uv = @{
            version = '0.12.18'
            url = 'https://github.com/astral-sh/uv/releases/download/0.12.18/uv-x86_64-pc-windows-msvc.zip'
            sha256 = 'cae6a3bc25239f83dffb467a4b180508d9da23986c04639ebfa44e43e6a84bff'
            executable = 'uv.exe'
            license = 'LICENSE-MIT'
            licenseSource = 'https://raw.githubusercontent.com/astral-sh/uv/01cb90c1a4f88af09906cb60de9766d2add4a062'
            licenseHashes = @{
                'LICENSE-MIT' = '860e3d7a86b84e6a7012c7a635fc64df475cebc6cce34dfeb73a5982ec58176c'
                'LICENSE-APACHE' = 'c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4'
            }
        }
        git = @{
            version = '2.56.0.windows.4'
            url = 'https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.4/MinGit-2.56.0.4-64-bit.zip'
            sha256 = 'e3bf78351fe777fba47ddee5950082f8a98bb0f02604fdc7ba3ee2cb68ffa877'
            executable = 'cmd\git.exe'
            license = 'LICENSE.txt'
        }
    }
    $helper = $helpers[$Name]
    $archive = Join-Path $script:InstallWork "$Name.zip"
    $destination = Join-Path $script:InstallWork "$Name-$($helper.version)"
    Get-BoundedDownload $helper.url $archive 134217728
    Assert-FileChecksum $archive $helper.sha256
    Expand-ContainedZip $archive $destination
    # uv's Windows ZIP contains executables only. Retain its official dual
    # license from the exact source commit, with separately inspected hashes.
    if ($helper.ContainsKey('licenseSource')) {
        foreach ($notice in $helper.licenseHashes.Keys) {
            $noticePath = Join-Path $destination $notice
            Get-BoundedDownload "$($helper.licenseSource)/$notice" $noticePath 1048576
            Assert-FileChecksum $noticePath $helper.licenseHashes[$notice]
        }
    }
    # Preserve the whole distribution, including all third-party notices.
    # A missing notice is a prerequisite failure, not permission to omit it.
    if (-not (Test-Path -LiteralPath (Join-Path $destination $helper.license))) {
        throw "Official $Name archive lacks the expected license notices; no helper was executed."
    }
    Write-InstallReceipt (Join-Path $destination 'bootstrap-receipt.json') $helper
    return (Join-Path $destination $helper.executable)
}

function Invoke-UnifiedInstall([string] $RequestedVersion, [bool] $SkipOpen, [bool] $PublicOnly) {
    $ErrorActionPreference = 'Stop'
    Set-StrictMode -Version 2.0
    Assert-NativePlatform
    $script:InstallWork = New-PrivateInstallDirectory
    $script:InstallLog = Join-Path $script:InstallWork 'install.log'
    $script:InstallStage = 'release qualification'
    $script:UseAnonymous = $PublicOnly -or $env:AMPLIFIER_GITHUB_ACCESS -eq 'anonymous'
    $originalTls = [Net.ServicePointManager]::SecurityProtocol
    try {
        Write-PrivateInstallText $script:InstallLog '' -Mutable
        [Net.ServicePointManager]::SecurityProtocol = $originalTls -bor [Net.SecurityProtocolType]::Tls12
        Write-Host "Review-first Windows setup. Private files: $($script:InstallWork)"
        Write-Host 'No system Python, machine PATH, execution policy, Git configuration or user data will be changed.'
        $api = 'https://api.github.com/repos/microsoft/amplifier-unified/releases'
        $releaseJson = Join-Path $script:InstallWork 'release.json'
        $selector = if ($RequestedVersion -eq 'latest') { 'latest' } else { 'tags/v' + $RequestedVersion.TrimStart('v') }
        Get-BoundedDownload "$api/$selector" $releaseJson 1048576
        $release = Get-Content -LiteralPath $releaseJson -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($release.draft -ne $false -or $release.prerelease -ne $false -or
            $release.tag_name -cnotmatch '^v([0-9]+\.[0-9]+\.[0-9]+)$') { throw 'Not an exact stable release.' }
        $resolved = $Matches[1]
        if ($RequestedVersion -ne 'latest' -and $resolved -cne $RequestedVersion.TrimStart('v')) {
            throw 'Requested release identity mismatch.'
        }
        # Resolve latest ONCE. Every subsequent URL is bound to the exact tag.
        $base = "https://github.com/microsoft/amplifier-unified/releases/download/v$resolved"
        $supportName = 'native-windows-support.json'
        if (@($release.assets | Where-Object { $_.name -ceq $supportName }).Count -ne 1) {
            throw "Release v$resolved has no native Windows support record. Keep your current installation; use the supported WSL setup."
        }
        $sumsPath = Join-Path $script:InstallWork 'SHA256SUMS'
        Get-BoundedDownload "$base/SHA256SUMS" $sumsPath 1048576
        $sums = [IO.File]::ReadAllText($sumsPath, [Text.Encoding]::UTF8)
        $supportPath = Join-Path $script:InstallWork $supportName
        Get-BoundedDownload "$base/$supportName" $supportPath 1048576
        Assert-FileChecksum $supportPath (Get-ExactChecksum $sums $supportName)
        $support = Read-NativeSupport (Get-Content -LiteralPath $supportPath -Raw -Encoding UTF8 | ConvertFrom-Json) $resolved
        $wheel = "amplifier_unified-$resolved-py3-none-any.whl"
        foreach ($asset in @($wheel, $support.hostConstraints, $support.defaultGraph)) {
            $path = Join-Path $script:InstallWork $asset
            $expected = Get-ExactChecksum $sums $asset
            Get-BoundedDownload "$base/$asset" $path 134217728
            Assert-FileChecksum $path $expected
        }
        $script:InstallStage = 'native activation prerequisites'
        Assert-NativeActivationContract

        # Reviewed installation path; unreachable until the owner integrates the
        # closed handoff above. Fixtures may replace functions, never production flags.
        $script:InstallStage = 'verified helpers'
        $childEnv = @{ PYTHONUTF8 = '1'; UV_NO_PROGRESS = '1'; UV_NO_CONFIG = '1' }
        $uv = Find-InstallHelper 'uv' $childEnv
        if (-not $uv) { $uv = Install-VerifiedHelper 'uv' }
        $git = Find-InstallHelper 'git' $childEnv
        if (-not $git) { $git = Install-VerifiedHelper 'git' }
        Write-InstallReceipt (Join-Path $script:InstallWork 'helpers-selected.json') @{
            uv = @{path = $uv; identity = (Invoke-InstallChild $uv @('--version') $childEnv)}
            git = @{path = $git; identity = (Invoke-InstallChild $git @('--version') $childEnv)}
        }
        $childEnv['PATH'] = [IO.Path]::GetDirectoryName($uv) + ';' + [IO.Path]::GetDirectoryName($git) + ';' + $env:PATH
        $childEnv['UV_NO_BUILD_PACKAGE'] = (@($support.nativePackages) -join ',')
        $script:InstallStage = 'managed CPython 3.13'
        $null = Invoke-InstallChild $uv @('python', 'install', '3.13', '--no-bin', '--no-registry') $childEnv
        $python = Invoke-InstallChild $uv @('python', 'find', '--no-project', '--managed-python', '3.13') $childEnv
        $null = Invoke-InstallChild $python @('-I', '-S', '-c',
            'import sys,sysconfig; assert sys.platform=="win32" and sys.version_info[:2]==(3,13) and not sysconfig.get_config_var("Py_GIL_DISABLED") and sys.maxsize>2**32') $childEnv
        $script:InstallStage = 'package installation'
        $arguments = @('tool', 'install', '--upgrade', '--managed-python', '--python', $python,
            '--constraints', (Join-Path $script:InstallWork $support.hostConstraints))
        foreach ($name in $support.nativePackages) { $arguments += @('--no-build-package', $name) }
        $arguments += (Join-Path $script:InstallWork $wheel)
        $null = Invoke-InstallChild $uv $arguments $childEnv
        $bin = Invoke-InstallChild $uv @('tool', 'dir', '--bin') $childEnv
        $launcher = Join-Path $bin 'amplifier-unified.exe'
        $toolRoot = Invoke-InstallChild $uv @('tool', 'dir') $childEnv
        $toolPython = Join-Path $toolRoot 'amplifier-unified\Scripts\python.exe'
        # Retain Git commits as well as distribution versions. Do not retain
        # credential-bearing URL strings from direct_url.json.
        $graph = Invoke-InstallChild $toolPython @('-I', '-c',
            'import importlib.metadata as m,json; rows=[dict(name=d.metadata["Name"],version=d.version,commit=json.loads(d.read_text("direct_url.json") or "{}").get("vcs_info",{}).get("commit_id")) for d in m.distributions()]; print(json.dumps(sorted(rows,key=lambda d:d["name"])))') $childEnv
        Write-PrivateInstallText (Join-Path $script:InstallWork 'installed-host-graph.json') $graph
        $script:InstallStage = 'user-logon lifecycle'
        # service install may report "current" without starting/restarting;
        # its exact lifecycle handoff must be supplied before the gate opens.
        $null = Invoke-InstallChild $launcher @('--no-open', 'service', 'install') $childEnv
        $status = (Invoke-InstallChild $launcher @('--no-open', 'service', 'status', '--wait', '120', '--json') $childEnv) | ConvertFrom-Json
        $doctor = (Invoke-InstallChild $launcher @('doctor', '--json') $childEnv) | ConvertFrom-Json
        if ($status.ok -ne $true -or $status.responding -ne $true -or
            $status.service -ne 'running' -or $doctor.ok -ne $true -or $doctor.version -cne $resolved) {
            throw 'Installed host is not ready. Run the absolute launcher with service status before retrying setup.'
        }
        $listener = [Uri]$status.url
        if ($listener.Scheme -ne 'https' -or -not $listener.IsLoopback -or
            $listener.UserInfo -or $listener.Query -or $listener.Fragment) {
            throw 'The lifecycle owner returned an unsafe listener address; no browser was opened.'
        }
        # No browser URL/token inspection in PowerShell. The Python owner alone
        # authenticates the listener and handles the one-use browser grant.
        if (-not $SkipOpen) { $null = Invoke-InstallChild $launcher @('open') $childEnv }
        Write-Host ("Host installation checked. Launch again: " + (Format-PowerShellLaunchCommand $launcher))
        Write-Host "Host listener: $($listener.AbsoluteUri)"
        Write-Host 'Default worker readiness and first response are separate; no first-response success is claimed.'
    } catch {
        # No raw exception/command output: logs must not contain credentials.
        $safe = if ($_.Exception.Message -match '^(Release v|Native activation integration|This exact release|Artifact checksum|Missing or duplicate|Malformed SHA256SUMS|Download failed|Child failed)') {
            $_.Exception.Message
        } else { 'Setup prerequisite failed. Review the failed stage before retrying.' }
        Write-PrivateInstallText $script:InstallLog "$($script:InstallStage): stopped. $safe`r`n" -Append
        throw "Installation stopped at $($script:InstallStage). $safe Private log: $($script:InstallLog)"
    } finally {
        [Net.ServicePointManager]::SecurityProtocol = $originalTls
        $script:InstallStorage.Dispose()
        # Keep private evidence AND helper directories: a generated lifecycle
        # may retain their absolute addresses. No automatic deletion or replay.
    }
}

function Read-PrototypeManifest($Record) {
    if ($Record.schemaVersion -ne 1 -or $Record.status -cne 'prototype' -or
        $Record.source -cnotmatch '^[0-9a-f]{40}$' -or
        $Record.version -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        $Record.python -cnotmatch '^3\.13\.[0-9]+$' -or
        $Record.architecture -cne 'x86_64' -or
        $Record.capsule.sha256 -cnotmatch '^[0-9a-f]{64}$') {
        throw 'Invalid prototype plan. Obtain an exact candidate plan from the trial owner.'
    }
    $uri = [Uri]$Record.capsule.url
    if ($uri.Scheme -cne 'https' -or $uri.DnsSafeHost -cne 'github.com' -or
        $uri.Port -ne 443 -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or
        $uri.AbsolutePath -cnotmatch '^/microsoft/amplifier-unified/releases/download/[^/]+/[^/]+\.zip$' -or
        $uri.AbsolutePath -match '/(latest|main)/') {
        throw 'Prototype capsule must name an exact official HTTPS release asset.'
    }
    $mode = Get-PrototypeSourceMode $Record
    if ($mode -eq 'pinned-vcs' -and ($Record.source -cne '1c4ac3b4f9ed5d2d3f588f6fa6c74a119e439153' -or
        $Record.version -cne '0.54.5')) {
        throw 'The pinned VCS prototype is bound to the frozen 0.54.5 candidate.'
    }
    return $Record
}

function Get-PrototypeSourceMode($Record) {
    # The original schema-1 offline prototype did not include a mode.
    $mode = if ($Record.PSObject.Properties.Name -contains 'sourceMode') { $Record.sourceMode } else { 'offline-wheels' }
    if ($mode -cne 'offline-wheels' -and $mode -cne 'pinned-vcs') {
        throw 'Unsupported prototype source mode.'
    }
    return $mode
}

function Get-PrototypeEnvironment([string] $Root, [string] $Uv, [string] $Git) {
    # Only child processes see these overrides. Never alter HOME/USERPROFILE or
    # the global uv tool installation. Git remains necessary for worker VCS sources.
    return @{
        PATH = [IO.Path]::GetDirectoryName($Uv) + ';' + [IO.Path]::GetDirectoryName($Git) + ';' + $env:PATH
        PYTHONUTF8 = '1'; PYTHONDONTWRITEBYTECODE = '1'
        UV_NO_CONFIG = '1'; UV_NO_PROGRESS = '1'; UV_NO_BUILD_PACKAGE = 'amplifier-core'
        UV_CACHE_DIR = Join-Path $Root 'cache'
        UV_TOOL_DIR = Join-Path $Root 'tools'
        UV_TOOL_BIN_DIR = Join-Path $Root 'tool-bin'
        UV_PYTHON_INSTALL_DIR = Join-Path $Root 'python'
        UV_PYTHON_BIN_DIR = Join-Path $Root 'python-bin'
        UV_HTTP_TIMEOUT = '30'; UV_HTTP_RETRIES = '2'
        TEMP = Join-Path $Root 'temp'; TMP = Join-Path $Root 'temp'
        AMPLIFIER_WEB_HOME = Join-Path $Root 'app'
        AMPLIFIER_HOME = Join-Path $Root 'shared'
        AMPLIFIER_SESSION_STATE_HOME = Join-Path $Root 'ownership'
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH = Join-Path $Root 'shared\projects'
        AMPLIFIER_UNIFIED_IMPORT_HOME = Join-Path $Root 'shared'
    }
}

function Write-PrototypeShortcut([string] $Python, [string] $Launcher) {
    # A user-owned link inside the private root, not a global shortcut or PATH edit.
    $path = Join-Path $script:InstallWork 'Launch Unified.lnk'
    $empty = $script:InstallStorage.CreateFile($path)
    $empty.Dispose()
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $Python
    $shortcut.Arguments = '-I -B ' + (ConvertTo-NativeArgument $Launcher)
    $shortcut.WorkingDirectory = $script:InstallWork
    $shortcut.Description = 'Experimental Amplifier Unified - foreground, Ctrl+C to stop'
    $shortcut.Save()
    $script:InstallStorage.HoldFile($path, $false)
    return $path
}

function Invoke-UnifiedPrototype([string] $ManifestUrl, [string] $ManifestHash, [bool] $SkipLaunch) {
    $ErrorActionPreference = 'Stop'
    Set-StrictMode -Version 2.0
    # Missing manifest/digest must refuse before any filesystem/network effects.
    if (-not $ManifestUrl -or $ManifestHash -cnotmatch '^[0-9a-f]{64}$') {
        throw 'Prototype setup requires its reviewed manifest URL and SHA-256.'
    }
    Assert-NativePlatform
    $script:InstallWork = New-PrivateInstallDirectory
    $script:InstallLog = Join-Path $script:InstallWork 'install.log'
    $script:InstallStage = 'prototype plan'
    $script:UseAnonymous = $true
    $originalTls = [Net.ServicePointManager]::SecurityProtocol
    $launchInfo = $null
    try {
        [Net.ServicePointManager]::SecurityProtocol = $originalTls -bor [Net.SecurityProtocolType]::Tls12
        Write-PrivateInstallText $script:InstallLog '' -Mutable
        Write-Host 'Experimental foreground setup, NOT a qualified public Windows installation.'
        Write-Host "Private location: $($script:InstallWork)"
        Write-Host 'Prior installs, PATH, default Python, trust stores and startup tasks stay unchanged.'
        $planPath = Join-Path $script:InstallWork 'prototype-plan.json'
        Get-BoundedDownload $ManifestUrl $planPath 1048576
        Assert-FileChecksum $planPath $ManifestHash
        $plan = Read-PrototypeManifest (Get-Content -LiteralPath $planPath -Raw -Encoding UTF8 | ConvertFrom-Json)
        $mode = Get-PrototypeSourceMode $plan
        Write-Host "Candidate $($plan.source), app $($plan.version), CPython $($plan.python)."
        $script:InstallStage = 'verified candidate capsule'
        $archive = Join-Path $script:InstallWork 'capsule.zip'
        Get-BoundedDownload $plan.capsule.url $archive 134217728
        Assert-FileChecksum $archive $plan.capsule.sha256
        $capsule = Join-Path $script:InstallWork 'capsule'
        Expand-ContainedZip $archive $capsule
        foreach ($name in @('requirements.txt', 'launch.py', 'capsule.json')) {
            $script:InstallStorage.HoldFile((Join-Path $capsule $name), $false)
        }
        $identity = Get-Content -LiteralPath (Join-Path $capsule 'capsule.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($identity.source -cne $plan.source -or $identity.version -cne $plan.version) {
            throw 'Prototype capsule identity mismatch. No package was installed.'
        }
        if ((Get-PrototypeSourceMode $identity) -cne $mode) {
            throw 'Prototype source mode differs from the reviewed plan.'
        }
        if ($mode -eq 'pinned-vcs') {
            foreach ($name in @('source_install.py', 'source-spec.json', 'uv.lock',
                'production-requirements.txt', 'git-requirements.txt', 'build-constraints.txt')) {
                $script:InstallStorage.HoldFile((Join-Path $capsule $name), $false)
            }
        }
        foreach ($relative in @('app', 'shared', 'ownership', 'shared\projects', 'workspace',
            'export', 'cache', 'python', 'python-bin', 'tools', 'tool-bin', 'temp')) {
            $script:InstallStorage.EnsureDirectory((Join-Path $script:InstallWork $relative))
        }
        # These are tool-owned mutation parents, not download/executable paths.
        # uv's absolute-target atomic renames otherwise conflict with our pins.
        foreach ($relative in @('', 'cache', 'python', 'python-bin', 'tools', 'tool-bin', 'temp')) {
            $path = if ($relative) { Join-Path $script:InstallWork $relative } else { $script:InstallWork }
            $script:InstallStorage.EnableToolWrites($path)
        }
        $script:InstallStage = 'verified portable tools'
        if ($mode -eq 'pinned-vcs') {
            Write-Host '[1/3] Acquiring verified private uv and standard MinGit; no developer tools required.'
        } else {
            Write-Host '[1/3] Finding compatible helpers; downloading private portable copies only if needed.'
        }
        $probeEnv = @{ PYTHONUTF8 = '1'; UV_NO_CONFIG = '1'; UV_NO_PROGRESS = '1' }
        if ($mode -eq 'pinned-vcs') {
            # The online source recipe is written against this exact private
            # helper, not whichever developer tool happens to be on PATH.
            $uv = Install-VerifiedHelper 'uv'
            $git = Install-VerifiedHelper 'git'
        } else {
            $uv = Find-InstallHelper 'uv' $probeEnv
            if (-not $uv) { $uv = Install-VerifiedHelper 'uv' }
            $git = Find-InstallHelper 'git' $probeEnv
            if (-not $git) { $git = Install-VerifiedHelper 'git' }
        }
        $childEnv = Get-PrototypeEnvironment $script:InstallWork $uv $git
        if ($mode -eq 'pinned-vcs') {
            $uvVersion = Invoke-InstallChild $uv @('--version') $childEnv
            if ($uvVersion -cnotmatch '^uv 0\.12\.18(?:\s|$)') { throw 'Pinned bootstrap uv identity mismatch.' }
        }
        $script:InstallStage = 'private ordinary CPython'
        Write-Host '[2/3] Preparing ordinary CPython 3.13 without changing the system Python.'
        # uv checks its pinned python-build-standalone digest. No registry/bin links.
        $null = Invoke-InstallChild $uv @('python', 'install', $plan.python, '--no-bin', '--no-registry') $childEnv
        $python = Invoke-InstallChild $uv @('python', 'find', '--no-project', '--managed-python', $plan.python) $childEnv
        if (-not $python.StartsWith((Join-Path $script:InstallWork 'python') + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Managed interpreter escaped the private prototype root.'
        }
        $null = Invoke-InstallChild $python @('-I', '-S', '-B', '-c',
            'import sys,sysconfig,struct; assert sys.platform=="win32" and ".".join(map(str,sys.version_info[:3]))==sys.argv[1] and sys.version_info[:2]==(3,13) and struct.calcsize("P")==8 and not sysconfig.get_config_var("Py_GIL_DISABLED")',
            $plan.python) $childEnv
        if ($mode -eq 'pinned-vcs') {
            $script:InstallStage = 'prototype source payload validation'
            $null = Invoke-InstallChild $python @('-I', '-S', '-B',
                (Join-Path $capsule 'source_install.py'), '--validate-source', $capsule) $childEnv
        }
        $script:InstallStage = 'offline immutable host installation'
        Write-Host '[3/3] Installing the verified host wheelhouse into a separate, noneditable environment.'
        $venv = Join-Path $script:InstallWork 'host'
        $null = Invoke-InstallChild $uv @('venv', '--python', $python, $venv) $childEnv
        $hostPython = Join-Path $venv 'Scripts\python.exe'
        $null = Invoke-InstallChild $uv @('pip', 'install', '--python', $hostPython,
            '--no-index', '--no-deps', '--require-hashes', '--only-binary', ':all:',
            '--find-links', (Join-Path $capsule 'wheels'), '-r', (Join-Path $capsule 'requirements.txt')) $childEnv
        if ($mode -eq 'pinned-vcs') {
            $script:InstallStage = 'prototype exact Git dependencies'
            Write-Host 'Building five frozen Python packages with private Git and pinned isolated backend wheels.'
            $gitNames = 'amplifier-foundation,amplifier-module-hooks-session-naming,amplifier-memory,amplifier-bundle-ui-use,amplifier-bundle-context-intelligence'
            $null = Invoke-InstallChild $uv @('pip', 'install', '--python', $hostPython,
                '--no-index', '--no-deps', '--no-require-hashes', '--verify-hashes',
                '--only-binary', ':all:', '--no-binary', $gitNames,
                '--find-links', (Join-Path $capsule 'build-wheels'),
                '--build-constraint', (Join-Path $capsule 'build-constraints.txt'),
                '-r', (Join-Path $capsule 'git-requirements.txt')) $childEnv
            $script:InstallStage = 'prototype installed source audit'
            $null = Invoke-InstallChild $hostPython @('-I', '-B',
                (Join-Path $capsule 'source_install.py'), '--audit-source', $capsule) $childEnv
        }
        $null = Invoke-InstallChild $uv @('pip', 'check', '--python', $hostPython) $childEnv
        $launcher = Join-Path $capsule 'launch.py'
        Write-InstallReceipt (Join-Path $script:InstallWork 'prototype.json') @{
            schemaVersion = 1; status = 'prototype'; source = $plan.source; version = $plan.version
            manifestSha256 = $ManifestHash; capsuleSha256 = $plan.capsule.sha256
            python = $hostPython; uv = $uv; git = $git
            sourceMode = $mode
            bootstrapUv = if ($mode -eq 'pinned-vcs') { '0.12.18' } else { 'reused-or-private' }
        }
        $null = Invoke-InstallChild $hostPython @('-I', '-B', $launcher, '--check') $childEnv
        $shortcutPath = Write-PrototypeShortcut $hostPython $launcher
        Write-Host "Setup prepared. Open this shortcut to launch or relaunch: $shortcutPath"
        Write-Host 'The first launch explains the CA trust decision; it never adds trust silently.'
        Write-Host 'Stop: Ctrl+C in the Unified window. Uninstall: stop, review/remove trial CA trust yourself, then remove only this printed private directory.'
        Write-Host 'Rollback: the previous installation was not replaced; keep using its original launcher.'
        if (-not $SkipLaunch) {
            $info = New-Object Diagnostics.ProcessStartInfo
            $info.FileName = $hostPython
            $info.Arguments = '-I -B ' + (ConvertTo-NativeArgument $launcher)
            $info.WorkingDirectory = $script:InstallWork
            $info.UseShellExecute = $true
            # Normal app filesystem owners take over after bootstrap validation;
            # no initializer races the bootstrap's strict app/state directory pins.
            $script:InstallStorage.Validate()
            $launchInfo = $info
        }
    } catch {
        # No raw exceptions or subprocess output: they may contain credentials.
        Write-PrivateInstallText $script:InstallLog "$($script:InstallStage): stopped; no automatic mutation retry.`r`n" -Append
        throw "Prototype stopped at $($script:InstallStage). Existing installs remain unchanged. Keep $($script:InstallWork) for review before retrying; partial files are retained."
    } finally {
        [Net.ServicePointManager]::SecurityProtocol = $originalTls
        $script:InstallStorage.Dispose()
    }
    if ($null -ne $launchInfo) {
        try {
            $null = [Diagnostics.Process]::Start($launchInfo)
            Write-Host 'Foreground launcher requested. Browser onboarding and first response are separate checks.'
        } catch {
            throw "Setup is prepared, but the launch request failed. Open the private shortcut yourself: $shortcutPath"
        }
    }
}

# Dot sourcing is only for offline function fixtures; it performs no setup.
if ($MyInvocation.InvocationName -ne '.') {
    if ($PrototypeManifestUrl -or $PrototypeManifestSha256) {
        Invoke-UnifiedPrototype $PrototypeManifestUrl $PrototypeManifestSha256 ([bool]$NoOpen)
    } else {
        Invoke-UnifiedInstall $Version ([bool]$NoOpen) ([bool]$Anonymous)
    }
}