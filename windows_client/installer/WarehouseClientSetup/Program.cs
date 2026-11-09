using System.Diagnostics;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using Microsoft.Win32;

namespace WarehouseClientSetup;

internal static class Program
{
    private const string AppName = "WarehouseClient";
    private const string DisplayName = "仓库物料系统客户端";
    private const string UninstallKeyPath = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\WarehouseClient";
    private const string RunKeyPath = @"Software\Microsoft\Windows\CurrentVersion\Run";

    [STAThread]
    private static void Main(string[] args)
    {
        Application.SetHighDpiMode(HighDpiMode.SystemAware);
        Application.EnableVisualStyles();

        try
        {
            if (args.Any(arg => arg.Equals("--uninstall", StringComparison.OrdinalIgnoreCase)))
            {
                RunUninstall(args);
                return;
            }

            Install();
        }
        catch (Exception ex)
        {
            MessageBox.Show(
                $"操作失败：\n{ex.Message}",
                DisplayName,
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
        }
    }

    private static void Install()
    {
        var installDir = GetInstallDir();
        PrepareExistingInstallation(installDir);

        Directory.CreateDirectory(installDir);
        ExtractPayload(installDir);

        var exePath = Path.Combine(installDir, "WarehouseClient.exe");
        var uninstallerPath = Path.Combine(installDir, "WarehouseClientUninstall.exe");
        File.Copy(Environment.ProcessPath!, uninstallerPath, overwrite: true);

        CreateShortcut(
            GetDesktopShortcutPath(),
            exePath,
            installDir,
            "打开仓库物料系统客户端");
        CreateShortcut(
            GetStartMenuShortcutPath(),
            exePath,
            installDir,
            "打开仓库物料系统客户端");
        CreateShortcut(
            GetUninstallShortcutPath(),
            uninstallerPath,
            installDir,
            "卸载仓库物料系统客户端",
            "--uninstall");

        RegisterUninstall(exePath, uninstallerPath, installDir);
        SetStartupEnabled(exePath, enabled: true);

        MessageBox.Show(
            $"安装完成。\n\n已安装到：{installDir}\n桌面快捷方式已创建。",
            DisplayName,
            MessageBoxButtons.OK,
            MessageBoxIcon.Information);

        Process.Start(new ProcessStartInfo(exePath) { UseShellExecute = true });
    }

    private static void PrepareExistingInstallation(string installDir)
    {
        var existingInstallDir = GetExistingInstallDir(installDir);
        var existingUninstaller = GetExistingUninstallerPath(existingInstallDir);
        var hasExistingClient = Directory.Exists(existingInstallDir)
            || File.Exists(existingUninstaller)
            || Registry.CurrentUser.OpenSubKey(UninstallKeyPath) is not null;

        if (!hasExistingClient)
        {
            return;
        }

        StopRunningClient(existingInstallDir);
        var settingsBackup = BackupClientSettings();

        var uninstalled = false;
        if (!string.IsNullOrWhiteSpace(existingUninstaller) && File.Exists(existingUninstaller))
        {
            uninstalled = RunExistingUninstaller(existingUninstaller, existingInstallDir);
        }

        if (!uninstalled)
        {
            CleanupExistingInstallation(existingInstallDir);
        }

        RestoreClientSettings(settingsBackup);
    }

    private static string GetExistingInstallDir(string fallbackInstallDir)
    {
        using var key = Registry.CurrentUser.OpenSubKey(UninstallKeyPath);
        var registeredDir = key?.GetValue("InstallLocation") as string;
        return string.IsNullOrWhiteSpace(registeredDir) ? fallbackInstallDir : registeredDir;
    }

    private static string GetExistingUninstallerPath(string installDir)
    {
        using var key = Registry.CurrentUser.OpenSubKey(UninstallKeyPath);
        foreach (var valueName in new[] { "QuietUninstallString", "UninstallString" })
        {
            var command = key?.GetValue(valueName) as string;
            var exePath = ExtractExecutablePath(command);
            if (!string.IsNullOrWhiteSpace(exePath) && File.Exists(exePath))
            {
                return exePath;
            }
        }

        return Path.Combine(installDir, "WarehouseClientUninstall.exe");
    }

    private static string ExtractExecutablePath(string? command)
    {
        if (string.IsNullOrWhiteSpace(command))
        {
            return "";
        }

        var text = command.Trim();
        if (text.StartsWith('"'))
        {
            var end = text.IndexOf('"', 1);
            return end > 1 ? text[1..end] : "";
        }

        var firstSpace = text.IndexOf(' ');
        return firstSpace > 0 ? text[..firstSpace] : text;
    }

    private static bool RunExistingUninstaller(string uninstallerPath, string installDir)
    {
        var tempUninstaller = Path.Combine(Path.GetTempPath(), $"WarehouseClientUninstall-{Guid.NewGuid():N}.exe");
        File.Copy(uninstallerPath, tempUninstaller, overwrite: true);

        try
        {
            using var process = Process.Start(new ProcessStartInfo(tempUninstaller)
            {
                Arguments = "--uninstall --temp --quiet",
                UseShellExecute = false,
            });

            if (process is null)
            {
                return false;
            }

            var deadline = DateTime.UtcNow.AddSeconds(45);
            while (DateTime.UtcNow < deadline)
            {
                if (process.WaitForExit(500))
                {
                    return process.ExitCode == 0 || !Directory.Exists(installDir);
                }

                if (!Directory.Exists(installDir))
                {
                    KillProcess(process);
                    return true;
                }
            }

            if (!Directory.Exists(installDir))
            {
                KillProcess(process);
                return true;
            }

            KillProcess(process);
            return false;
        }
        finally
        {
            DeleteFileIfExists(tempUninstaller);
        }
    }

    private static void CleanupExistingInstallation(string installDir)
    {
        StopRunningClient(installDir);
        RemoveShortcuts();
        RemoveRegistryEntries();
        DeleteDirectoryIfExists(installDir);
    }

    private static string BackupClientSettings()
    {
        var settingsDir = GetSettingsDir();
        if (!Directory.Exists(settingsDir))
        {
            return "";
        }

        var backupDir = Path.Combine(Path.GetTempPath(), $"WarehouseClientSettings-{Guid.NewGuid():N}");
        CopyDirectory(settingsDir, backupDir);
        return backupDir;
    }

    private static void RestoreClientSettings(string backupDir)
    {
        if (string.IsNullOrWhiteSpace(backupDir) || !Directory.Exists(backupDir))
        {
            return;
        }

        var settingsDir = GetSettingsDir();
        if (Directory.Exists(settingsDir))
        {
            DeleteDirectoryIfExists(settingsDir);
        }

        CopyDirectory(backupDir, settingsDir);
        DeleteDirectoryIfExists(backupDir);
    }

    private static void CopyDirectory(string sourceDir, string targetDir)
    {
        Directory.CreateDirectory(targetDir);

        foreach (var directory in Directory.EnumerateDirectories(sourceDir, "*", SearchOption.AllDirectories))
        {
            var relative = Path.GetRelativePath(sourceDir, directory);
            Directory.CreateDirectory(Path.Combine(targetDir, relative));
        }

        foreach (var file in Directory.EnumerateFiles(sourceDir, "*", SearchOption.AllDirectories))
        {
            var relative = Path.GetRelativePath(sourceDir, file);
            var target = Path.Combine(targetDir, relative);
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            File.Copy(file, target, overwrite: true);
        }
    }

    private static void KillProcess(Process process)
    {
        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
                process.WaitForExit(5000);
            }
        }
        catch
        {
            // Best effort; installer cleanup has a manual fallback.
        }
    }

    private static void RunUninstall(string[] args)
    {
        var installDir = GetInstallDir();
        var runningFromInstallDir = IsPathUnderDirectory(Environment.ProcessPath ?? "", installDir);
        var isTempUninstaller = args.Any(arg => arg.Equals("--temp", StringComparison.OrdinalIgnoreCase));
        var quiet = args.Any(arg =>
            arg.Equals("--quiet", StringComparison.OrdinalIgnoreCase)
            || arg.Equals("/quiet", StringComparison.OrdinalIgnoreCase)
            || arg.Equals("--silent", StringComparison.OrdinalIgnoreCase)
            || arg.Equals("/silent", StringComparison.OrdinalIgnoreCase));

        if (runningFromInstallDir && !isTempUninstaller)
        {
            if (!quiet && !ConfirmUninstall())
            {
                return;
            }

            var tempUninstaller = Path.Combine(Path.GetTempPath(), $"WarehouseClientUninstall-{Guid.NewGuid():N}.exe");
            File.Copy(Environment.ProcessPath!, tempUninstaller, overwrite: true);
            var argsText = quiet ? "--uninstall --temp --quiet" : "--uninstall --temp";
            Process.Start(new ProcessStartInfo(tempUninstaller, argsText) { UseShellExecute = true });
            return;
        }

        if (!isTempUninstaller && !quiet && !ConfirmUninstall())
        {
            return;
        }

        StopRunningClient(installDir);
        RemoveShortcuts();
        RemoveRegistryEntries();
        DeleteDirectoryIfExists(GetSettingsDir());
        DeleteDirectoryIfExists(installDir);

        if (!quiet)
        {
            MessageBox.Show(
                "卸载完成。",
                DisplayName,
                MessageBoxButtons.OK,
                MessageBoxIcon.Information);
        }
    }

    private static bool ConfirmUninstall()
    {
        return MessageBox.Show(
            "确定要卸载仓库物料系统客户端吗？",
            DisplayName,
            MessageBoxButtons.YesNo,
            MessageBoxIcon.Question) == DialogResult.Yes;
    }

    private static void ExtractPayload(string installDir)
    {
        using var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("WarehouseClientPayload.zip")
            ?? throw new InvalidOperationException("安装包缺少客户端文件。");
        using var archive = new ZipArchive(stream, ZipArchiveMode.Read);

        foreach (var entry in archive.Entries)
        {
            if (string.IsNullOrWhiteSpace(entry.Name))
            {
                continue;
            }

            var targetPath = Path.GetFullPath(Path.Combine(installDir, entry.FullName));
            if (!IsPathUnderDirectory(targetPath, installDir))
            {
                throw new InvalidOperationException("安装包包含非法路径。");
            }

            Directory.CreateDirectory(Path.GetDirectoryName(targetPath)!);
            entry.ExtractToFile(targetPath, overwrite: true);
        }
    }

    private static void RegisterUninstall(string exePath, string uninstallerPath, string installDir)
    {
        using var key = Registry.CurrentUser.CreateSubKey(UninstallKeyPath);
        key.SetValue("DisplayName", DisplayName);
        key.SetValue("DisplayVersion", GetApplicationVersion());
        key.SetValue("DisplayIcon", exePath);
        key.SetValue("InstallLocation", installDir);
        key.SetValue("Publisher", "Warehouse");
        key.SetValue("UninstallString", $"\"{uninstallerPath}\" --uninstall");
        key.SetValue("QuietUninstallString", $"\"{uninstallerPath}\" --uninstall --quiet");
        key.SetValue("NoModify", 1, RegistryValueKind.DWord);
        key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
    }

    private static string GetApplicationVersion()
    {
        return Assembly.GetExecutingAssembly().GetName().Version?.ToString() ?? "1.0.0";
    }

    private static void RemoveRegistryEntries()
    {
        Registry.CurrentUser.DeleteSubKey(UninstallKeyPath, throwOnMissingSubKey: false);

        using var runKey = Registry.CurrentUser.OpenSubKey(RunKeyPath, writable: true);
        runKey?.DeleteValue(AppName, throwOnMissingValue: false);
    }

    private static void SetStartupEnabled(string exePath, bool enabled)
    {
        using var runKey = Registry.CurrentUser.OpenSubKey(RunKeyPath, writable: true);
        if (runKey is null)
        {
            return;
        }

        if (enabled)
        {
            runKey.SetValue(AppName, $"\"{exePath}\"");
        }
        else
        {
            runKey.DeleteValue(AppName, throwOnMissingValue: false);
        }
    }

    private static void StopRunningClient(string installDir)
    {
        foreach (var process in Process.GetProcessesByName("WarehouseClient"))
        {
            try
            {
                var modulePath = process.MainModule?.FileName ?? "";
                if (!IsPathUnderDirectory(modulePath, installDir))
                {
                    continue;
                }

                process.CloseMainWindow();
                if (!process.WaitForExit(2000))
                {
                    process.Kill(entireProcessTree: true);
                    process.WaitForExit(5000);
                }
            }
            catch
            {
                // Continue uninstalling the rest of the client.
            }
        }
    }

    private static void RemoveShortcuts()
    {
        DeleteFileIfExists(GetDesktopShortcutPath());
        DeleteFileIfExists(GetStartMenuShortcutPath());
        DeleteFileIfExists(GetUninstallShortcutPath());
    }

    private static void CreateShortcut(
        string shortcutPath,
        string targetPath,
        string workingDirectory,
        string description,
        string arguments = "")
    {
        Directory.CreateDirectory(Path.GetDirectoryName(shortcutPath)!);
        var shellType = Type.GetTypeFromProgID("WScript.Shell")
            ?? throw new InvalidOperationException("无法创建快捷方式：WScript.Shell 不可用。");
        dynamic shell = Activator.CreateInstance(shellType)!;
        dynamic shortcut = shell.CreateShortcut(shortcutPath);
        shortcut.TargetPath = targetPath;
        shortcut.Arguments = arguments;
        shortcut.WorkingDirectory = workingDirectory;
        shortcut.Description = description;
        shortcut.IconLocation = targetPath;
        shortcut.Save();
        Marshal.FinalReleaseComObject(shortcut);
        Marshal.FinalReleaseComObject(shell);
    }

    private static string GetInstallDir()
    {
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), AppName);
    }

    private static string GetSettingsDir()
    {
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), AppName);
    }

    private static string GetDesktopShortcutPath()
    {
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), $"{DisplayName}.lnk");
    }

    private static string GetStartMenuShortcutPath()
    {
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.StartMenu), "Programs", $"{DisplayName}.lnk");
    }

    private static string GetUninstallShortcutPath()
    {
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.StartMenu), "Programs", $"卸载{DisplayName}.lnk");
    }

    private static bool IsPathUnderDirectory(string path, string directory)
    {
        if (string.IsNullOrWhiteSpace(path) || string.IsNullOrWhiteSpace(directory))
        {
            return false;
        }

        var fullPath = Path.GetFullPath(path);
        var fullDirectory = Path.GetFullPath(directory).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        return fullPath.StartsWith(fullDirectory, StringComparison.OrdinalIgnoreCase);
    }

    private static void DeleteFileIfExists(string path)
    {
        try
        {
            if (File.Exists(path))
            {
                File.Delete(path);
            }
        }
        catch
        {
            // Best effort cleanup.
        }
    }

    private static void DeleteDirectoryIfExists(string path)
    {
        if (Directory.Exists(path))
        {
            Directory.Delete(path, recursive: true);
        }
    }
}
