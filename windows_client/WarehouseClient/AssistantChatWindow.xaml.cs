using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Windows.Interop;
using System.Text.Json;
using System.Windows;
using Microsoft.Web.WebView2.Core;

namespace WarehouseClient;

public partial class AssistantChatWindow : Window
{
    private bool _isExiting;

    [DllImport("user32.dll")]
    private static extern bool ReleaseCapture();

    [DllImport("user32.dll")]
    private static extern IntPtr SendMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
    public event Action<string>? ActivityChanged;

    public AssistantChatWindow()
    {
        InitializeComponent();
        Closing += OnClosing;
    }

    public async Task InitializeAsync(CoreWebView2Environment environment, Uri serverUri)
    {
        // 与主窗口共用 WebView2 会话，使用当前登录账号。
        await AssistantBrowser.EnsureCoreWebView2Async(environment);
        AssistantBrowser.CoreWebView2.WebMessageReceived += OnWebMessage;
        AssistantBrowser.Source = new Uri(serverUri, "/assistant/desktop");
    }

    public async Task ShowAssistantAsync()
    {
        Show();
        WindowState = WindowState.Normal;
        Activate();
        if (AssistantBrowser.CoreWebView2 is not null)
            await AssistantBrowser.CoreWebView2.ExecuteScriptAsync("document.getElementById('aiFloatBox')?.classList.add('open')");
    }

    private void OnWebMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        using var message = JsonDocument.Parse(e.WebMessageAsJson);
        if (message.RootElement.ValueKind != JsonValueKind.Object || !message.RootElement.TryGetProperty("type", out var type)) return;
        if (type.GetString() == "assistant-close") Hide();
        else if (type.GetString() == "assistant-drag")
        {
            // WebView 占用客户区，由网页头部触发原生窗口拖动。
            ReleaseCapture();
            SendMessage(new WindowInteropHelper(this).Handle, 0x00A1, new IntPtr(2), IntPtr.Zero);
        }
        else if (type.GetString() == "assistant-state" && message.RootElement.TryGetProperty("state", out var state))
            ActivityChanged?.Invoke(state.GetString() ?? "idle");
    }

    private void OnClosing(object? sender, CancelEventArgs e)
    {
        if (_isExiting) return;
        e.Cancel = true;
        Hide();
    }

    public void CloseForExit()
    {
        _isExiting = true;
        Close();
    }
}
