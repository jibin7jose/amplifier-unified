"""Portable source guards plus real PowerShell fixtures when available.

Source guards are not native installation qualification. The executable fixture
is skipped, not simulated, on hosts without native Windows PowerShell.
"""

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/install.ps1"
FIXTURE = ROOT / "tests/windows/install.Tests.ps1"


class WindowsInstallerSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SCRIPT.read_text(encoding="utf-8")

    def function(self, name):
        match = re.search(
            rf"(?ms)^function {re.escape(name)}\b.*?(?=^function |\Z)",
            self.source,
        )
        self.assertIsNotNone(match, name)
        return match.group()

    def test_review_first_parameters_and_ps51_syntax_baseline(self):
        self.assertTrue(self.source.startswith("#requires -Version 5.1"))
        for parameter in ("$Version = 'latest'", "[switch] $NoOpen", "[switch] $Anonymous"):
            self.assertIn(parameter, self.source)
        self.assertIn("[CmdletBinding()]", self.source)  # builtin -Verbose
        self.assertIn("Do not change execution policy", self.source)
        self.assertNotIn("??", self.source)
        self.assertNotIn("ForEach-Object -Parallel", self.source)

    def test_actual_os_and_processor_not_shell_environment(self):
        function = self.function("Assert-NativePlatform")
        for required in (
            "Win32_OperatingSystem", "Win32_Processor", "$os.ProductType -ne 1",
            "$architectures[0] -ne 9", "[Environment]::Is64BitProcess", "22000",
        ):
            self.assertIn(required, function)
        self.assertNotIn("PROCESSOR_ARCHITECTURE", function)

    def test_existing_release_rejected_before_helpers_or_package_mutation(self):
        main = self.function("Invoke-UnifiedInstall")
        refusal = main.index("has no native Windows support record")
        for effect in ("Find-InstallHelper", "Install-VerifiedHelper", "'tool', 'install'"):
            self.assertLess(refusal, main.index(effect))
        self.assertEqual(main.count('Get-BoundedDownload "$api/$selector"'), 1)
        self.assertIn("$release.prerelease -ne $false", main)
        self.assertIn("$release.draft -ne $false", main)
        self.assertIn("Get-ExactChecksum $sums $supportName", main)

    def test_activation_handoff_is_closed_without_force_bypass(self):
        gate = self.function("Assert-NativeActivationContract")
        self.assertIn("throw 'Native activation integration is pending", gate)
        self.assertNotIn("return", gate)
        main = self.function("Invoke-UnifiedInstall")
        self.assertLess(main.index("Assert-NativeActivationContract"), main.index("'tool', 'install'"))
        self.assertNotIn("$env:AMPLIFIER_FORCE", self.source)

    def test_checksum_parser_demands_exact_unique_names(self):
        parser = self.function("Get-ExactChecksum")
        self.assertIn("$Matches[2] -ceq $Name", parser)
        self.assertIn("$matchesForName.Count -ne 1", parser)
        self.assertIn("Malformed SHA256SUMS", parser)
        self.assertIn("Get-FileHash -LiteralPath $Path -Algorithm SHA256", self.source)

    def test_downloads_stream_with_explicit_size_time_redirect_tls_limits(self):
        download = self.function("Get-BoundedDownload")
        for required in (
            "Invoke-WebRequest", "-UseBasicParsing", "-TimeoutSec 30",
            "$attempt -lt 2", "$redirect -le 5", "$current.Scheme -ne 'https'",
            "$current.UserInfo", "$allowedHosts -notcontains",
            "$request.AllowAutoRedirect = $false", "$request.ReadWriteTimeout = 30000",
            "$total -gt $MaxBytes", "$clock.Elapsed.TotalSeconds -gt 180",
            "$script:InstallStorage.CreateFile($part)", "$firstFailure.Exception.GetType().Name",
        ):
            self.assertIn(required, download)
        self.assertNotIn("-SkipCertificateCheck", self.source)
        self.assertNotIn("ServerCertificateValidationCallback", self.source)

    def test_archives_prevalidate_all_paths_before_extraction(self):
        unzip = self.function("Expand-ContainedZip")
        self.assertLess(unzip.index("$target.StartsWith"), unzip.index(".CreateFile($target)"))
        for required in (
            "OrdinalIgnoreCase", "$seen.Add", "$unixType -eq 40960",
            "ExternalAttributes -band 1024", "536870912", "20000",
            "CON|PRN|AUX|NUL", "$script:InstallStorage.CreateDirectory($Destination)",
        ):
            self.assertIn(required, unzip)
        self.assertNotIn("Test-Path", unzip)
        self.assertNotIn("[IO.Directory]::CreateDirectory", unzip)
        self.assertNotIn("::ExtractToFile", unzip)
        self.assertIn("$inputStream.CopyTo($output)", unzip)
        self.assertIn("$script:InstallStorage.HoldFile($target, $false)", unzip)

    def test_zip_assemblies_are_explicit_before_first_type_use_in_both_editions(self):
        core_load = "Add-Type -AssemblyName System.IO.Compression -ErrorAction Stop\n"
        filesystem_load = "Add-Type -AssemblyName System.IO.Compression.FileSystem\n"
        unzip = self.function("Expand-ContainedZip")
        fixture = FIXTURE.read_text(encoding="utf-8")
        for source, use in (
            (unzip, "[IO.Compression.ZipFile]::OpenRead"),
            (fixture, "[IO.Compression.ZipArchiveMode]::Create"),
        ):
            with self.subTest(use=use):
                self.assertIn(core_load, source)
                self.assertIn(filesystem_load, source)
                self.assertIn(use, source)
                self.assertLess(source.index(core_load), source.index(filesystem_load))
                self.assertLess(source.index(filesystem_load), source.index(use))

    def test_shadow_ancestor_refusal_starts_with_valid_private_parent_and_leaf(self):
        fixture = FIXTURE.read_text(encoding="utf-8")
        self.assertIn("$shadowStorage = New-Object UnifiedBootstrap.InstallStorage($root)", fixture)
        self.assertIn("$appendShadow = Join-Path $root 'append-shadow.exe'", fixture)
        shadow = fixture.split("$shadowStorage = New-Object UnifiedBootstrap.InstallStorage($root)", 1)[1].split(
            "$appendShadow = Join-Path $root 'append-shadow.exe'", 1,
        )[0]
        for required in (
            "$shadowParent = $shadowStorage.Root",
            "$shadow = Join-Path $shadowParent 'shadow.exe'",
            "$parentAcl = Get-Acl -LiteralPath $shadowParent",
            "Assert-Equal ($parentAcl.GetOwner([Security.Principal.SecurityIdentifier]).Value) ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value)",
            "Assert-Equal $parentAcl.AreAccessRulesProtected $true",
            "$stream = $shadowStorage.CreateFile($shadow)",
            "Assert-Equal $stream.Length 0", "Assert-PrivateFileAcl $shadow",
            "$stream.Write($bytes, 0, $bytes.Length)", "$stream.Flush($true)",
            "$shadowStorage.TrustExecutable($shadow)", "$shadowStorage.Validate()",
            "finally { $shadowStorage.Dispose() }",
            "$parentAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($foreign, 'WriteData', 'Allow')))",
            "Set-Acl -LiteralPath $shadowParent -AclObject $parentAcl",
            "Assert-Refused { Invoke-InstallChild $shadow @('--version') @{} } 'Foreign mutation'",
        ):
            self.assertIn(required, shadow)
        self.assertLess(shadow.index("$parentAcl.GetOwner("), shadow.index("$shadowStorage.CreateFile("))
        self.assertLess(shadow.index("$parentAcl.AreAccessRulesProtected"), shadow.index("$shadowStorage.CreateFile("))
        self.assertLess(shadow.index("Assert-PrivateFileAcl $shadow"), shadow.index("$stream.Write("))
        self.assertLess(shadow.index("$stream.Dispose()"), shadow.index("$shadowStorage.TrustExecutable("))
        self.assertLess(shadow.index("$shadowStorage.Validate()"), shadow.index("$shadowStorage.Dispose()"))
        self.assertLess(shadow.index("$shadowStorage.Dispose()"), shadow.index("$parentAcl.AddAccessRule("))
        self.assertLess(shadow.index("Set-Acl"), shadow.rindex("Assert-PrivateFileAcl $shadow"))
        self.assertLess(shadow.rindex("Assert-PrivateFileAcl $shadow"), shadow.index("Invoke-InstallChild"))
        self.assertNotIn("[IO.Directory]::CreateDirectory", shadow)
        self.assertNotIn("$fixtureStorage.CreateDirectory", shadow)  # no retained unsafe-parent pin
        self.assertNotIn("'Untrusted bootstrap owner", shadow)  # do not broaden the oracle
        append = fixture.split("$appendShadow = Join-Path $root 'append-shadow.exe'", 1)[1]
        self.assertIn("FileSystemAccessRule($foreign, 'AppendData', 'Allow')", append)
        self.assertIn("Assert-Refused { Invoke-InstallChild $appendShadow @('--version') @{} } 'Foreign mutation'", append)

    def test_helpers_have_inspected_official_exact_assets_and_notices(self):
        helper = self.function("Install-VerifiedHelper")
        for value in (
            "https://github.com/astral-sh/uv/releases/download/0.12.18/uv-x86_64-pc-windows-msvc.zip",
            "cae6a3bc25239f83dffb467a4b180508d9da23986c04639ebfa44e43e6a84bff",
            "https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.4/MinGit-2.56.0.4-64-bit.zip",
            "e3bf78351fe777fba47ddee5950082f8a98bb0f02604fdc7ba3ee2cb68ffa877",
            "LICENSE.txt", "LICENSE-MIT", "LICENSE-APACHE", "bootstrap-receipt.json",
            "01cb90c1a4f88af09906cb60de9766d2add4a062",
        ):
            self.assertIn(value, helper)
        self.assertLess(helper.index("Assert-FileChecksum $archive"), helper.index("Expand-ContainedZip"))
        self.assertNotIn("winget", helper)

    def test_log_directory_acl_exists_at_exclusive_creation(self):
        private = self.function("Initialize-InstallStorage")
        self.assertIn('CreateDirectoryW(path, ref attributes)', private)
        self.assertIn('"D:P(A;OICI;FA;;;"', private)
        self.assertIn("info.attributes & 0x400", private)
        self.assertNotIn("Set-Acl", private)
        main = self.function("Invoke-UnifiedInstall")
        self.assertLess(main.index("New-PrivateInstallDirectory"), main.index("Write-PrivateInstallText"))

    def test_existing_ancestor_mutation_acl_and_handle_identity_are_checked(self):
        private = self.function("Initialize-InstallStorage")
        for required in (
            "GetSecurityInfo(pin.handle", "new RawSecurityDescriptor", "Foreign mutation",
            "const uint Mutation = 0x500D0156u", "Mutation & ~4u",
            "AceFlags.InheritOnly", "ControlFlags.DiscretionaryAclPresent",
            "GetFileInformationByHandle", "info.high != pin.high",
            "GetFinalPathNameByHandleW", "GetVolumeInformationByHandleW",
            'fs.ToString() != "NTFS"', "GetDriveTypeW", "fixed local NTFS",
            "mutable ? 3u : 1u", "0x02200000", "order[i].handle.Dispose()",
        ):
            self.assertIn(required, private)
        self.assertLess(private.index("parent = Address(parent); Traverse(parent)"),
                        private.index("CreateExact(Root)"))
        self.assertNotIn("Directory.CreateDirectory(", private)
        self.assertNotIn("SetAccessRule", private)

    def test_directory_pins_request_list_access_for_sharing_enforcement(self):
        private = self.function("Initialize-InstallStorage")
        self.assertIn(
            "CreateFileW(path, 0x20080u | (directory ? 1u : 0x80000000u)",
            private,
        )
        self.assertIn("mutable ? 3u : 1u", private)  # never share DELETE
        self.assertNotIn("directory ? 0u", private)  # metadata-only is not a pin

    def test_private_file_creation_sets_security_and_validates_before_stream_exposure(self):
        private = self.function("Initialize-InstallStorage")
        self.assertIn("public FileStream CreateFile(string path) {", private)
        create = private.split("public FileStream CreateFile(string path) {", 1)[1].split(
            "public FileStream OpenAppend", 1,
        )[0]
        for required in (
            'EntryPoint="CreateFileW"', "ref Attributes security",
            'sd.Owner.Value != owner',
        ):
            self.assertIn(required, private)
        for required in (
            "AssertOwnedPath(path)", '"O:" + owner + "D:P(A;;FA;;;"',
            '")(A;;FA;;;SY)"', "inherit=false", "LocalFree(descriptor)",
            "ref attributes, 1, 0x00200080",  # CREATE_NEW + OPEN_REPARSE_POINT
            "CreatePrivateFileW(path, 0xC0020080u, 1u",  # no WRITE/DELETE sharing
            "Inspect(new Pin {path=path, handle=handle, privateAcl=true})",
            "new FileStream(handle, FileAccess.ReadWrite)",
            "error == 80 || error == 183", "never adopted", "handle.Dispose()",
        ):
            self.assertIn(required, create)
        self.assertLess(create.index("AssertOwnedPath"), create.index("CreatePrivateFileW"))
        self.assertLess(create.index('"O:" + owner'), create.index("CreatePrivateFileW"))
        self.assertLess(create.index("Inspect("), create.index("new FileStream"))
        self.assertNotIn("Set-Acl", self.source)
        self.assertNotIn("SetAccessControl", self.source)

    def test_append_requires_existing_mutable_pin_and_revalidates_open_handle(self):
        private = self.function("Initialize-InstallStorage")
        self.assertIn("public FileStream OpenAppend(string path) {", private)
        append = private.split("public FileStream OpenAppend(string path) {", 1)[1].split(
            "public void PublishFile", 1,
        )[0]
        for required in (
            "pins.TryGetValue(path, out pin)", "!pin.mutable", "Verify(pin)",
            "IntPtr.Zero, 3, 0x00200000",  # OPEN_EXISTING, never create on append
            "Inspect(new Pin", "info.volume != pin.volume", "info.high != pin.high",
            "info.low != pin.low", "stream.Seek(0, SeekOrigin.End)",
        ):
            self.assertIn(required, append)
        self.assertLess(append.index("Verify(pin)"), append.index("CreateFileW"))
        self.assertLess(append.index("info.volume != pin.volume"), append.index("new FileStream"))

    def test_publication_uses_native_same_directory_leaf_without_weakening_pins(self):
        private = self.function("Initialize-InstallStorage")
        self.assertIn("public void PublishFile(string source, string destination) {", private)
        publish = private.split("public void PublishFile(string source, string destination) {", 1)[1].split(
            "public void HoldFile", 1,
        )[0]
        for required in (
            "AssertOwnedPath(source); AssertOwnedPath(destination)",
            "Path.GetDirectoryName(source), Path.GetDirectoryName(destination)",
            "String.Equals(source, destination", "pins.ContainsKey(source)",
            "CreateFileW(source, 0x80030080u, 1u",  # DELETE, no WRITE/DELETE sharing
            "IntPtr.Zero, 3, 0x00200000",  # OPEN_EXISTING + OPEN_REPARSE_POINT
            "Inspect(new Pin {path=source", "Inspect(new Pin {path=destination",
            "Encoding.Unicode.GetBytes(Path.GetFileName(destination))",
            "Marshal.Copy(new byte[size], 0, buffer, size)",  # replace=false, root=NULL
            'Marshal.OffsetOf(typeof(RenameInfo), "length")',
            'Marshal.OffsetOf(typeof(RenameInfo), "name")',
            "NtSetInformationFile(handle, out io, buffer, (uint)size, 10)",
            "if (status < 0)", "RtlNtStatusToDosError(status)",
            "before.volume != after.volume", "before.high != after.high", "before.low != after.low",
            "publication completed but validation failed", "Marshal.FreeHGlobal(buffer)",
        ):
            self.assertIn(required, publish)
        self.assertLess(publish.index("Inspect(new Pin {path=source"), publish.index("NtSetInformationFile("))
        self.assertLess(publish.index("NtSetInformationFile("), publish.index("Inspect(new Pin {path=destination"))
        self.assertNotIn("SetFileInformationByHandle(", publish)  # Win32 expands relative names
        self.assertNotIn("Dispose()", publish)  # never release the parent pins
        self.assertNotIn("MoveFile", publish)
        self.assertNotIn("Thread.Sleep", private)
        self.assertNotIn("GC.Collect", self.source)

    def test_native_fixture_distinguishes_handle_closure_from_directory_sharing(self):
        fixture = FIXTURE.read_text(encoding="utf-8")
        for required in (
            "$createdHandle = $stream.SafeFileHandle",
            "Assert-Equal $createdHandle.IsClosed $false",
            "Assert-Equal $createdHandle.IsClosed $true",
            "Assert-Equal $stream.CanWrite $false",
            "Assert-SharingRefused 'absolute-file-move-parent-pin'",
            "$fixtureStorage.PublishFile($part, $published)",
            "$fixtureStorage.PublishFile($candidate, $winner)",
            "'collision.part'", "'collision.bin'",
            "$fixtureStorage.PublishFile($published, $candidate)",
            "\n    [IO.File]::Move($part, $published)",
            "\n    [IO.File]::Move($published, $part)",
        ):
            self.assertIn(required, fixture)
        close = fixture.index("Assert-Equal $createdHandle.IsClosed $true")
        pin_conflict = fixture.index("Assert-SharingRefused 'absolute-file-move-parent-pin'")
        publish = fixture.index("$fixtureStorage.PublishFile($part, $published)")
        release = fixture.index("$fixtureStorage.Dispose()")
        forward = fixture.index("\n    [IO.File]::Move($part, $published)")
        reverse = fixture.index("\n    [IO.File]::Move($published, $part)")
        self.assertLess(close, pin_conflict)
        self.assertLess(pin_conflict, publish)
        self.assertLess(publish, release)
        self.assertLess(release, reverse)
        self.assertLess(reverse, forward)
        self.assertLess(forward, fixture.index("Write-Output 'OFFLINE FIXTURE PASS"))
        self.assertNotIn("GC]::Collect", fixture)
        self.assertNotIn("Start-Sleep", fixture)

    def test_actual_downloader_is_exercised_offline_before_orchestration_stubs(self):
        factory = self.function("New-InstallDownloadRequest")
        self.assertIn("return [Net.HttpWebRequest]::Create($Uri)", factory)
        download = self.function("Get-BoundedDownload")
        self.assertIn("$request = New-InstallDownloadRequest $current", download)
        fixture = FIXTURE.read_text(encoding="utf-8")
        offline = fixture.index("$requestFactory = ${function:New-InstallDownloadRequest}")
        stub = fixture.index("function Get-BoundedDownload(")
        real_checks = fixture[offline:stub]
        for required in (
            "function New-InstallDownloadRequest([Uri] $Uri)",
            "Add-Member ScriptMethod GetResponseStream",
            "Add-Member ScriptMethod GetResponse",
            "Get-BoundedDownload 'https://github.com/offline-fixture' $download 1000000",
            "('stream' * 12000)", "'oversize'", "'incomplete'",
            "'part-collision'", "'destination-collision'",
            "Assert-Equal $script:OfflineRequests 1",
            "Assert-Equal $script:OfflineRequests 2",
            "'part winner unchanged'", "'destination winner unchanged'",
            "Assert-Equal $inputStream.CanRead $false",
            "Assert-Equal $response.Disposed $true",
            "finally { ${function:New-InstallDownloadRequest} = $requestFactory }",
        ):
            self.assertIn(required, real_checks)
        self.assertNotIn("[Net.HttpWebRequest]", real_checks)
        self.assertNotIn("TcpListener", fixture)

    def test_native_fixture_checks_empty_file_security_and_valid_state_routes(self):
        fixture = FIXTURE.read_text(encoding="utf-8")
        self.assertIn("$stream = $fixtureStorage.CreateFile($part)", fixture)
        self.assertIn("$payload.CopyTo($stream)", fixture)
        start = fixture.index("$stream = $fixtureStorage.CreateFile($part)")
        before_bytes = fixture[start:fixture.index("$payload.CopyTo($stream)", start)]
        self.assertIn("Assert-Equal $stream.Length 0", before_bytes)
        self.assertIn("Assert-PrivateFileAcl $part", before_bytes)
        for required in (
            "GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $owner",
            "-not $acl.AreAccessRulesProtected", "$rule.IsInherited",
            "$seen.Count -ne 2", "$seen -notcontains 'S-1-5-18'",
            "Write-PrivateInstallText $body 'unchanged'",
            "Write-PrivateInstallText $Destination $body",
            "Assert-PrivateFileAcl (Join-Path $destination 'ok.txt')",
            "$fixtureStorage.HoldFile($archivePath, $false)",
            "Write-PrivateInstallText $missingLog 'must not create' -Append",
            "Write-PrivateInstallText $body 'must not append' -Append",
            "Assert-Equal ([IO.File]::Exists($missingLog)) $false",
            "'private stream'", "'firstsecond'",
        ):
            self.assertIn(required, fixture)
        # Negative executable fixtures start private; only the intended grant
        # is then changed. Default leaf ownership must not confound the oracle.
        self.assertNotIn("[IO.File]::WriteAllText(", fixture)
        self.assertNotIn("[IO.Compression.ZipFile]::Open($archivePath", fixture)
        self.assertIn("$acl.AddAccessRule(", fixture)  # retain foreign-append negative

    def test_native_fixture_tags_refusals_and_checks_exact_private_owner(self):
        fixture = FIXTURE.read_text(encoding="utf-8")
        for tag in (
            "pinned-ancestor-rename", "pinned-private-root-rename",
            "hashed-file-append", "hashed-file-rename", "extraction-root-rename",
        ):
            self.assertIn(f"Assert-SharingRefused '{tag}'", fixture)
        self.assertNotRegex(fixture, r"Assert-SharingRefused\s+\{")
        self.assertIn("Fixture [$Case]", fixture)
        self.assertIn("$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value", fixture)
        self.assertIn("Import-Module Microsoft.PowerShell.Security -ErrorAction Stop", fixture)
        # Keep the successful post-dispose rename as the refusal's control.
        self.assertIn("finally { $pinned.Dispose() }", fixture)
        control = fixture.index("\n    [IO.Directory]::Move($ancestor, ($ancestor + '-replacement'))")
        # Disposing just the nested context is not enough to establish that
        # the fixture no longer owns overlapping traversal/descendant pins.
        release = fixture.index("$fixtureStorage.Dispose()")
        reverse = fixture.index("[IO.Directory]::Move(($ancestor + '-replacement'), $ancestor)")
        passed = fixture.index("Write-Output 'OFFLINE FIXTURE PASS")
        cleanup = fixture.index("Remove-Item -LiteralPath $root -Recurse -Force")
        self.assertLess(fixture.index("finally { $pinned.Dispose() }"), release)
        self.assertLess(fixture.index("'Artifact checksum mismatch'"), release)
        self.assertLess(release, control)
        self.assertLess(control, reverse)
        self.assertLess(reverse, passed)
        self.assertLess(passed, cleanup)
        self.assertIn("Write-Output $_.InvocationInfo.PositionMessage", fixture)
        self.assertIn("Write-Output $_.ScriptStackTrace", fixture)

    def test_fixture_launcher_module_path_is_child_only_and_edition_specific(self):
        # Exercise the real launcher with only process execution mocked.
        inherited = {
            "PSModulePath": r"C:\fixture\PowerShell\7\Modules",
            "pSmOdUlEpAtH": r"C:\fixture\other-modules",
            "PATH": r"C:\fixture\bin",
            "FIXTURE_MARKER": "unchanged",
        }
        with mock.patch.object(os, "environ", inherited):
            before = dict(os.environ)
            for name in ("powershell.exe", "pwsh.exe"):
                with self.subTest(name=name), mock.patch.object(
                    shutil, "which", return_value=rf"C:\fixture\{name}",
                ), mock.patch.object(subprocess, "run") as run:
                    run.return_value = subprocess.CompletedProcess(
                        [], 0, "OFFLINE FIXTURE PASS", "",
                    )
                    case = NativePowerShellInstallerTests(
                        "test_offline_fixture_windows_powershell_51",
                    )
                    case.run_fixture(name)
                expected = {
                    key: value for key, value in before.items()
                    if name != "powershell.exe" or key.upper() != "PSMODULEPATH"
                }
                self.assertIn("env", run.call_args.kwargs)
                self.assertEqual(run.call_args.kwargs["env"], expected)
                self.assertEqual(dict(os.environ), before)
                self.assertNotIn("-ExecutionPolicy", run.call_args.args[0])

    def test_pins_cover_writes_hashes_extraction_and_existing_executable_probes(self):
        writer = self.function("Write-PrivateInstallText")
        self.assertLess(writer.index("AssertOwnedPath"), writer.index(".CreateFile($Path)"))
        self.assertLess(writer.index(".CreateFile($Path)"), writer.index("$stream.Write"))
        self.assertIn("$script:InstallStorage.OpenAppend($Path)", writer)
        self.assertNotIn("[IO.File]::Open", self.source)
        self.assertNotIn("[IO.FileMode]::Append", writer)
        download = self.function("Get-BoundedDownload")
        self.assertIn("$script:InstallStorage.Validate()", download)
        self.assertIn("HoldFile($Destination, $false)", download)
        self.assertIn(".PublishFile($part, $Destination)", download)
        self.assertLess(download.index(".CreateFile($part)"), download.index("$output.Write"))
        self.assertIn("if ($partCreated -and [IO.File]::Exists($part)) { [IO.File]::Delete($part) }", download)
        self.assertLess(download.index(".CreateFile($part)"), download.index("$partCreated = $true"))
        self.assertLess(download.index("$output.Dispose()"), download.index(".PublishFile($part, $Destination)"))
        self.assertLess(download.index(".PublishFile($part, $Destination)"), download.index("$partCreated = $false", download.index(".PublishFile(")))
        self.assertNotIn("[IO.File]::Move", download)
        checksum = self.function("Assert-FileChecksum")
        self.assertLess(checksum.index("HoldFile"), checksum.index("Get-FileHash"))
        child = self.function("Invoke-InstallChild")
        self.assertLess(child.index("TrustExecutable($Executable)"), child.index("$process.Start()"))
        main = self.function("Invoke-UnifiedInstall")
        self.assertLess(main.index("stopped. $safe"), main.index("$script:InstallStorage.Dispose()"))
        self.assertIn("$script:InstallStorage.Dispose()", main[main.index("} finally {"):])

    def test_printed_launcher_uses_powershell_not_crt_quoting(self):
        formatter = self.function("Format-PowerShellLaunchCommand")
        self.assertIn('$Executable.Replace("\'", "\'\'")', formatter)
        self.assertNotIn("ConvertTo-NativeArgument", formatter)
        main = self.function("Invoke-UnifiedInstall")
        self.assertIn("Format-PowerShellLaunchCommand $launcher", main)
        fixture = FIXTURE.read_text(encoding="utf-8")
        for marker in (
            "O'Brien", "Parser]::ParseInput", "CommandElements[0].Value",
            "DeleteSubdirectoriesAndFiles", "WriteAttributes", "TakeOwnership",
            "ReadAndExecute", "Assert-SharingRefused", "never adopted",
            "CreateJunction", "outside remains unchanged", "shadow.exe",
        ):
            self.assertIn(marker, fixture)

    def test_child_only_anonymous_env_and_no_output_or_browser_token_logging(self):
        child = self.function("Invoke-InstallChild")
        for key in (
            "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_COUNT",
            "credential.helper", "core.askPass", "GH_TOKEN", "GITHUB_TOKEN",
        ):
            self.assertIn(key, child)
        self.assertIn("$info.EnvironmentVariables", child)
        self.assertIn("$info.UseShellExecute = $false", child)
        self.assertNotIn("SetEnvironmentVariable", self.source)
        self.assertNotRegex(self.source, r"\$env:\w+\s*=(?!=)")
        self.assertNotIn("control-token", self.source)
        self.assertNotIn("Write-Host $output", child)
        self.assertNotIn("AppendAllText($script:InstallLog, $output", child)

    def test_positive_child_probe_uses_validated_host_and_native_crt_argv(self):
        fixture = FIXTURE.read_text(encoding="utf-8")
        self.assertIn("    $probeHost = (Get-Command ", fixture)
        self.assertIn("    # Atomic creation/owner DACL", fixture)
        probe = fixture.split("    $probeHost = (Get-Command ", 1)[1].split(
            "    # Atomic creation/owner DACL", 1,
        )[0]
        for required in (
            "'pwsh.exe' -CommandType Application -ErrorAction Stop).Source",
            "$probeFile = Join-Path $script:InstallWork 'crt-probe.ps1'",
            "DllImportSearchPath.System32", 'DllImport("msvcrt.dll"',
            'EntryPoint="__wgetmainargs"', "CallingConvention.Cdecl",
            "GetMainArgs(out count, out argv, out env, 0, ref startup)",
            "count < 6 || count > 64 || argv == IntPtr.Zero",
            "Marshal.PtrToStringUni(Marshal.ReadIntPtr(argv, i * IntPtr.Size))",
            "$raw = [OfflineCrt]::Read()",
            "$raw[4] -cne $PSCommandPath", "$raw[5] -cne '--fixture-values'",
            "values = @($raw | Select-Object -Skip 6)",
            "[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)",
            "Write-PrivateInstallText $probeFile $probe", "Assert-PrivateFileAcl $probeFile",
            "Assert-FileChecksum $probeFile $probeHash",
            "$fixtureStorage.TrustExecutable($probeHost)", "$fixtureStorage.Validate()",
            "$probeEnvironment = @{ PSModulePath = Join-Path ([IO.Path]::GetDirectoryName($probeHost)) 'Modules' }",
            "Invoke-InstallChild $probeHost (@('-NoProfile', '-NonInteractive', '-File', $probeFile, '--fixture-values') + $values) $probeEnvironment",
            "Assert-Equal $observed.parser 'msvcrt.__wgetmainargs'",
            "cwd = [Environment]::CurrentDirectory",
            "Assert-Equal $observed.cwd $script:InstallWork",
            "Assert-Equal $observed.globalConfig 'NUL'",
            "Assert-Equal $observed.anonymous 'anonymous'",
            "Assert-Equal $observed.tokenAbsent $true",
            "for ($i = 0; $i -lt $values.Count; $i++) { Assert-Equal $observed.values[$i] $values[$i] }",
            "Assert-Equal $env:GH_TOKEN $beforeToken",
            "Assert-Equal $env:GIT_CONFIG_GLOBAL $beforeConfig",
            "Assert-Equal $env:PATH $beforePath",
            "Assert-Equal $env:PSModulePath $beforeModules",
        ):
            self.assertIn(required, probe)
        launch = probe.index("Invoke-InstallChild $probeHost")
        for security in (
            "Write-PrivateInstallText $probeFile", "Assert-PrivateFileAcl $probeFile",
            "Assert-FileChecksum $probeFile", "$fixtureStorage.TrustExecutable($probeHost)",
            "$fixtureStorage.Validate()",
        ):
            self.assertLess(probe.index(security), launch)
        self.assertLess(launch, probe.rindex("Assert-FileChecksum $probeFile"))
        self.assertLess(launch, probe.rindex("$fixtureStorage.Validate()"))
        self.assertNotIn("$PythonExecutable", probe)
        self.assertNotIn("$args", probe)  # the oracle must bypass PS argument binding
        self.assertNotIn("GetCommandLineArgs", probe)  # not CLR's argv either
        self.assertNotIn("-ExecutionPolicy", probe)
        self.assertNotRegex(probe, r"\$env:PSModulePath\s*=")

    def test_install_selects_managed_ordinary_313_without_native_fallback(self):
        main = self.function("Invoke-UnifiedInstall")
        self.assertIn("'python', 'install', '3.13', '--no-bin', '--no-registry'", main)
        self.assertIn("'--managed-python', '3.13'", main)
        self.assertIn('not sysconfig.get_config_var("Py_GIL_DISABLED")', main)
        self.assertIn("'--no-build-package', $name", main)
        self.assertIn("@($support.nativePackages)", main)
        self.assertNotRegex(main, r"'--no-build'\s*[,)]")
        self.assertEqual(main.count("'tool', 'install'"), 1)
        self.assertNotIn("ReadToEndAsync", main)

    def test_lifecycle_uses_current_cli_without_replacing_unmanaged_task(self):
        main = self.function("Invoke-UnifiedInstall")
        self.assertIn("@('--no-open', 'service', 'install')", main)
        self.assertIn("@('--no-open', 'service', 'status', '--wait', '120', '--json')", main)
        self.assertIn("@('doctor', '--json')", main)
        self.assertIn("if (-not $SkipOpen)", main)
        self.assertNotIn("'--replace'", main)
        self.assertNotIn("Register-ScheduledTask", self.source)
        self.assertNotIn("Start-Process", self.source)


@unittest.skipUnless(sys.platform == "win32", "native Windows PowerShell fixtures pending")
class NativePowerShellInstallerTests(unittest.TestCase):
    def run_fixture(self, name):
        shell = shutil.which(name)
        if not shell:
            self.skipTest(f"{name} unavailable; no native pass claimed")
        # pwsh -> Python -> powershell.exe does not get pwsh's direct-child
        # PSModulePath sanitization. Let 5.1 rebuild its own defaults; leave the
        # parent process and the PowerShell 7 fixture's environment unchanged.
        child_env = {
            key: value for key, value in os.environ.items()
            if name != "powershell.exe" or key.upper() != "PSMODULEPATH"
        }
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-File", str(FIXTURE),
             "-PythonExecutable", sys.executable],
            cwd=ROOT, text=True, capture_output=True, timeout=90,
            env=child_env,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OFFLINE FIXTURE PASS", result.stdout)

    def test_offline_fixture_windows_powershell_51(self):
        self.run_fixture("powershell.exe")

    def test_offline_fixture_powershell_7(self):
        self.run_fixture("pwsh.exe")


if __name__ == "__main__":
    unittest.main()