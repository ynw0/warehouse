using System.Runtime.InteropServices;
using System.Text;
using WinForms = System.Windows.Forms;

namespace WarehouseClient;

internal static class AssistantWorkVisibility
{
    [StructLayout(LayoutKind.Sequential)]
    private struct NativeRect { public int Left, Top, Right, Bottom; }

    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll")] private static extern bool IsZoomed(IntPtr window);
    [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr window, out NativeRect rect);
    [DllImport("user32.dll")] private static extern IntPtr MonitorFromWindow(IntPtr window, uint flags);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetClassName(IntPtr window, StringBuilder name, int count);

    public static bool ShouldHide(IntPtr assistantWindow)
    {
        var foreground = GetForegroundWindow();
        if (foreground == IntPtr.Zero) return false;
        GetWindowThreadProcessId(foreground, out var processId);
        // 助手自身窗口和系统桌面不触发免打扰。
        if (processId == (uint)Environment.ProcessId) return false;
        var name = new StringBuilder(256);
        GetClassName(foreground, name, name.Capacity);
        if (name.ToString() is "Progman" or "WorkerW" or "Shell_TrayWnd" or "Shell_SecondaryTrayWnd") return false;
        // 原生窗口坐标和显示器边界均为设备像素，避免混用 WPF 缩放单位。
        if (MonitorFromWindow(foreground, 2) != MonitorFromWindow(assistantWindow, 2)) return false;
        if (IsZoomed(foreground)) return true;
        if (!GetWindowRect(foreground, out var rect)) return false;
        var screen = WinForms.Screen.FromHandle(assistantWindow).Bounds;
        return rect.Left <= screen.Left && rect.Top <= screen.Top
            && rect.Right >= screen.Right && rect.Bottom >= screen.Bottom;
    }
}
