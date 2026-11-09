using System.Text.Json;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.Net;
using System.Net.NetworkInformation;
using Microsoft.Web.WebView2.Core;
using Microsoft.Win32;
using System.Windows;
using System.Windows.Threading;
using WinForms = System.Windows.Forms;

namespace WarehouseClient;

public partial class MainWindow : Window
{
    private const string TodoNotificationTitle = "物料流程提醒";
    private const string TodoNotificationText = "您有新的物料流程需要办理，请及时处理！";

    private readonly DispatcherTimer _pollTimer = new();
    private readonly HashSet<string> _seenNotifications = [];
    private readonly HashSet<string> _seenSystemNotifications = [];
    private ApiClient _apiClient;
    private AppSettings _settings;
    private WinForms.NotifyIcon? _trayIcon;
    private AssistantBallWindow? _assistantBall;
    private AssistantChatWindow? _assistantWindow;
    private bool _openingAssistant;
    private Action? _lastBalloonClickAction;
    private CancellationTokenSource? _streamCts;
    private CancellationTokenSource? _recoveryCts;
    private bool _isFirstPoll = true;
    private bool _isFirstSystemNotificationPoll = true;
    private bool _isExiting;
    private bool _isPolling;
    private int _connectionFailureCount;
    private bool _browserPermissionsConfigured;

    public MainWindow(ApiClient apiClient, AppSettings settings)
    {
        InitializeComponent();
        _apiClient = apiClient;
        _settings = settings;
        ServerText.Text = _apiClient.ServerUri.ToString().TrimEnd('/');

        _pollTimer.Interval = TimeSpan.FromSeconds(Math.Max(15, _settings.PollSeconds));
        _pollTimer.Tick += async (_, _) => await PollNotificationsAsync(showInitialSummary: false);
        SystemEvents.PowerModeChanged += SystemEvents_PowerModeChanged;
        NetworkChange.NetworkAvailabilityChanged += NetworkChange_NetworkAvailabilityChanged;
    }

    public async Task InitializeAsync()
    {
        SetupTrayIcon();
        await InitializeBrowserAsync();
        _assistantBall = new AssistantBallWindow(() => _ = OpenAssistantAsync(), ShowMainWindow, MarkAllReadAsync);
        _assistantBall.LocationChanged += (_, _) => PositionAssistantWindow();
        _assistantBall.Show();
        await PollNotificationsAsync(showInitialSummary: true);
        StartRealtimeNotifications();
        _pollTimer.Start();
    }

    private async Task InitializeBrowserAsync()
    {
        var environment = await CreateBrowserEnvironmentAsync();
        await Browser.EnsureCoreWebView2Async(environment);
        ConfigureBrowserPermissions();
        await ApplyCookiesToWebViewAsync();
        Browser.CoreWebView2.Settings.AreDefaultContextMenusEnabled = true;
        Browser.CoreWebView2.Settings.AreDevToolsEnabled = true;
        Browser.Source = _apiClient.ServerUri;
    }

    private async Task<CoreWebView2Environment> CreateBrowserEnvironmentAsync()
    {
        var options = new CoreWebView2EnvironmentOptions
        {
            AdditionalBrowserArguments = BuildBrowserArguments(),
        };
        return await CoreWebView2Environment.CreateAsync(null, null, options);
    }

    private string BuildBrowserArguments()
    {
        if (!_apiClient.ServerUri.Scheme.Equals("http", StringComparison.OrdinalIgnoreCase))
        {
            return "";
        }

        var trustedOrigin = _apiClient.ServerUri.GetLeftPart(UriPartial.Authority);
        return "--unsafely-treat-insecure-origin-as-secure=" + trustedOrigin;
    }

    private void ConfigureBrowserPermissions()
    {
        if (Browser.CoreWebView2 is null || _browserPermissionsConfigured)
        {
            return;
        }

        Browser.CoreWebView2.PermissionRequested += Browser_PermissionRequested;
        Browser.CoreWebView2.WebMessageReceived += Browser_AssistantState;
        _browserPermissionsConfigured = true;
    }

    private void Browser_AssistantState(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        using var message = JsonDocument.Parse(e.WebMessageAsJson);
        if (message.RootElement.ValueKind != JsonValueKind.Object) return;
        if (message.RootElement.TryGetProperty("type", out var type) && type.GetString() == "assistant-state"
            && message.RootElement.TryGetProperty("state", out var state))
            _assistantBall?.SetActivity(state.GetString() ?? "idle");
    }

    private void Browser_PermissionRequested(object? sender, CoreWebView2PermissionRequestedEventArgs e)
    {
        if (e.PermissionKind == CoreWebView2PermissionKind.Camera)
        {
            e.State = CoreWebView2PermissionState.Allow;
        }
        else if (e.PermissionKind == CoreWebView2PermissionKind.Notifications)
        {
            // 客户端统一由悬浮球和托盘投递提醒，避免网页重复弹出系统通知。
            e.State = CoreWebView2PermissionState.Deny;
        }
    }

    private async Task ApplyCookiesToWebViewAsync()
    {
        if (Browser.CoreWebView2 is null)
        {
            return;
        }

        foreach (Cookie cookie in _apiClient.Cookies)
        {
            var webCookie = Browser.CoreWebView2.CookieManager.CreateCookie(
                cookie.Name,
                cookie.Value,
                _apiClient.ServerUri.Host,
                string.IsNullOrWhiteSpace(cookie.Path) ? "/" : cookie.Path);
            webCookie.IsHttpOnly = cookie.HttpOnly;
            webCookie.IsSecure = cookie.Secure;
            Browser.CoreWebView2.CookieManager.AddOrUpdateCookie(webCookie);
        }

        await Task.CompletedTask;
    }

    private void SetupTrayIcon()
    {
        _trayIcon = new WinForms.NotifyIcon
        {
            Text = "仓库物料系统",
            Icon = System.Drawing.Icon.ExtractAssociatedIcon(Process.GetCurrentProcess().MainModule?.FileName ?? "") ?? SystemIcons.Application,
            Visible = true,
            ContextMenuStrip = BuildTrayMenu(),
        };
        _trayIcon.DoubleClick += (_, _) => ShowMainWindow();
        _trayIcon.BalloonTipClicked += (_, _) => (_lastBalloonClickAction ?? OpenTodoPage)();
    }

    private WinForms.ContextMenuStrip BuildTrayMenu()
    {
        var menu = new WinForms.ContextMenuStrip();
        menu.Items.Add("打开系统", null, (_, _) => ShowMainWindow());
        menu.Items.Add("刷新待办", null, async (_, _) => await PollNotificationsAsync(showInitialSummary: true));
        menu.Items.Add("设置", null, (_, _) => OpenSettingsWindow());
        menu.Items.Add("重新登录", null, async (_, _) => await ReloginAsync());
        menu.Items.Add(new WinForms.ToolStripSeparator());
        menu.Items.Add("退出", null, (_, _) =>
        {
            _isExiting = true;
            Close();
        });
        return menu;
    }

    private async Task<bool> PollNotificationsAsync(bool showInitialSummary)
    {
        if (_isPolling)
        {
            return false;
        }

        _isPolling = true;
        try
        {
            var payload = await _apiClient.GetNotificationsAsync();
            HandleNotificationItems(payload.Items, showInitialSummary);
            HandleSystemNotificationItems(payload.Notifications, showInitialSummary);
            MarkConnectionHealthy();
            return true;
        }
        catch (UnauthorizedAccessException ex)
        {
            _pollTimer.Stop();
            ShowNotification("登录已失效", ex.Message, openTodoOnClick: false);
            return false;
        }
        catch (Exception ex)
        {
            MarkConnectionIssue($"待办检查失败：{ex.Message}");
            return false;
        }
        finally
        {
            _isPolling = false;
        }
    }

    private void MarkConnectionHealthy()
    {
        _connectionFailureCount = 0;
        ServerText.Text = _apiClient.ServerUri.ToString().TrimEnd('/');
        _pollTimer.Interval = TimeSpan.FromSeconds(Math.Max(15, _settings.PollSeconds));
    }

    private void MarkConnectionIssue(string message)
    {
        _connectionFailureCount++;
        _pollTimer.Interval = TimeSpan.FromSeconds(15);
        if (!_pollTimer.IsEnabled)
        {
            _pollTimer.Start();
        }

        var retryText = _connectionFailureCount == 1 ? "正在自动重连" : $"正在自动重连，第 {_connectionFailureCount} 次";
        ServerText.Text = $"{_apiClient.ServerUri.ToString().TrimEnd('/')}  |  {message}，{retryText}";
    }

    private void SystemEvents_PowerModeChanged(object sender, PowerModeChangedEventArgs e)
    {
        if (e.Mode == PowerModes.Resume)
        {
            _ = Dispatcher.InvokeAsync(new Action(async () => await RecoverConnectionAsync("电脑已唤醒，正在重新连接服务器", TimeSpan.FromSeconds(3))));
        }
    }

    private void NetworkChange_NetworkAvailabilityChanged(object? sender, NetworkAvailabilityEventArgs e)
    {
        _ = Dispatcher.InvokeAsync(new Action(async () =>
        {
            if (e.IsAvailable)
            {
                await RecoverConnectionAsync("网络已恢复，正在重新连接服务器", TimeSpan.FromSeconds(1));
                return;
            }

            MarkConnectionIssue("网络连接已断开");
        }));
    }

    private async Task RecoverConnectionAsync(string message, TimeSpan delay)
    {
        var oldRecovery = _recoveryCts;
        oldRecovery?.Cancel();
        oldRecovery?.Dispose();
        var cts = new CancellationTokenSource();
        _recoveryCts = cts;

        try
        {
            ServerText.Text = $"{_apiClient.ServerUri.ToString().TrimEnd('/')}  |  {message}";
            _streamCts?.Cancel();
            if (delay > TimeSpan.Zero)
            {
                await Task.Delay(delay, cts.Token);
            }

            StartRealtimeNotifications();
            if (!_pollTimer.IsEnabled)
            {
                _pollTimer.Start();
            }

            if (await PollNotificationsAsync(showInitialSummary: false))
            {
                ReloadBrowserAfterReconnect();
            }
        }
        catch (OperationCanceledException)
        {
            // A newer recovery attempt replaced this one.
        }
    }

    private void ReloadBrowserAfterReconnect()
    {
        if (Browser.CoreWebView2 is null)
        {
            return;
        }

        if (Browser.Source is null)
        {
            Browser.Source = _apiClient.ServerUri;
            return;
        }

        Browser.CoreWebView2.Reload();
    }

    private void StartRealtimeNotifications()
    {
        _streamCts?.Cancel();
        _streamCts = new CancellationTokenSource();
        _ = Task.Run(() => RunNotificationStreamAsync(_streamCts.Token));
    }

    private async Task RunNotificationStreamAsync(CancellationToken cancellationToken)
    {
        var delaySeconds = 3;
        while (!cancellationToken.IsCancellationRequested)
        {
            try
            {
                await _apiClient.StreamNotificationsAsync(async payload =>
                {
                    await Dispatcher.InvokeAsync(() =>
                    {
                        HandleNotificationItems(payload.Items, showInitialSummary: false);
                        HandleSystemNotificationItems(payload.Notifications, showInitialSummary: false);
                        MarkConnectionHealthy();
                    });
                }, cancellationToken);
                delaySeconds = 3;
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                break;
            }
            catch (UnauthorizedAccessException ex)
            {
                await Dispatcher.InvokeAsync(() =>
                {
                    _pollTimer.Stop();
                    ShowNotification("登录已失效", ex.Message, openTodoOnClick: false);
                });
                break;
            }
            catch (Exception ex)
            {
                await Dispatcher.InvokeAsync(() =>
                {
                    MarkConnectionIssue($"实时连接中断：{ex.Message}");
                });
            }

            try
            {
                await Task.Delay(TimeSpan.FromSeconds(delaySeconds), cancellationToken);
                delaySeconds = Math.Min(60, delaySeconds * 2);
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }
    }

    private void HandleNotificationItems(IReadOnlyList<NotificationItem> items, bool showInitialSummary)
    {
        var newItems = items.Where(item => _seenNotifications.Add(item.Key)).ToList();

        if (_isFirstPoll)
        {
            _isFirstPoll = false;
            if (showInitialSummary && items.Count > 0)
            {
                ShowNotification(TodoNotificationTitle, $"您有 {items.Count} 个物料流程待办，请及时处理。", openTodoOnClick: true);
            }
            return;
        }

        foreach (var item in newItems)
        {
            var title = !string.IsNullOrWhiteSpace(item.NotificationTitle)
                ? item.NotificationTitle
                : string.IsNullOrWhiteSpace(item.FormNo)
                    ? TodoNotificationTitle
                    : $"{item.FormNo} {TodoNotificationTitle}";
            var body = string.IsNullOrWhiteSpace(item.NotificationBody)
                ? TodoNotificationText
                : item.NotificationBody;
            ShowNotification(title, body, openTodoOnClick: true);
        }
    }

    private void HandleSystemNotificationItems(IReadOnlyList<SystemNotificationItem> items, bool showInitialSummary)
    {
        var newItems = items.Where(item => _seenSystemNotifications.Add(item.Key)).ToList();

        if (_isFirstSystemNotificationPoll)
        {
            _isFirstSystemNotificationPoll = false;
            if (showInitialSummary && items.Count > 0)
            {
                ShowNotification("系统通知", $"您有 {items.Count} 条未读通知", OpenNotificationsPage, isSystemNotice: true);
            }
            return;
        }

        foreach (var item in newItems)
        {
            var title = string.IsNullOrWhiteSpace(item.Title) ? "系统通知" : item.Title;
            var body = string.IsNullOrWhiteSpace(item.Body) ? title : item.Body;
            ShowNotification(title, body, OpenNotificationsPage, isSystemNotice: true);
        }
    }


    private void ShowNotification(string title, string body, bool openTodoOnClick)
    {
        ShowNotification(title, body, openTodoOnClick ? OpenTodoPage : null);
    }

    private void ShowNotification(string title, string body, Action? clickAction, bool isSystemNotice = false)
    {
        _lastBalloonClickAction = clickAction;
        // 免打扰状态同时抑制托盘气泡，不能绕过桌面形象的隐藏规则。
        var suppressed = _assistantBall?.RefreshWorkVisibility() == true;
        if (_settings.ShowTrayBalloon && !suppressed)
        {
            _trayIcon?.ShowBalloonTip(6000, title, body, WinForms.ToolTipIcon.Info);
        }

        if (_settings.ShowDesktopToast)
            _assistantBall?.EnqueueNotice(title, body, clickAction, isSystemNotice);
    }

    private async Task OpenAssistantAsync()
    {
        if (_openingAssistant) return;
        _openingAssistant = true;
        try
        {
            if (Browser.CoreWebView2 is null)
                throw new InvalidOperationException("客户端页面尚未准备好。");
            if (_assistantWindow is null)
            {
                var window = new AssistantChatWindow();
                window.ActivityChanged += state => _assistantBall?.SetActivity(state);
                try
                {
                    window.Show();
                    await window.InitializeAsync(Browser.CoreWebView2.Environment, _apiClient.ServerUri);
                    _assistantWindow = window;
                }
                catch
                {
                    window.CloseForExit();
                    throw;
                }
            }
            PositionAssistantWindow();
            await _assistantWindow.ShowAssistantAsync();
        }
        catch (Exception ex)
        {
            _assistantBall?.EnqueueNotice("打开小助手失败", ex.Message, null, false);
        }
        finally
        {
            _openingAssistant = false;
        }
    }

    private void PositionAssistantWindow()
    {
        if (_assistantBall is null || _assistantWindow is null || !_assistantBall.IsLoaded) return;
        var area = _assistantBall.CurrentWorkArea();
        var left = _assistantBall.Left - _assistantWindow.Width - 8;
        if (left < area.Left) left = _assistantBall.Left + _assistantBall.Width + 8;
        _assistantWindow.Left = Math.Clamp(left, area.Left, Math.Max(area.Left, area.Right - _assistantWindow.Width));
        _assistantWindow.Top = Math.Clamp(_assistantBall.Top, area.Top, Math.Max(area.Top, area.Bottom - _assistantWindow.Height));
    }

    private async Task MarkAllReadAsync()
    {
        try
        {
            await _apiClient.MarkAllNotificationsReadAsync();
            _assistantBall?.ClearSystemNotices();
            await PollNotificationsAsync(showInitialSummary: false);
        }
        catch (Exception ex)
        {
            _assistantBall?.EnqueueNotice("一键已读失败", ex.Message, null, false);
        }
    }

    private void ShowMainWindow()
    {
        Show();
        WindowState = WindowState.Normal;
        Activate();
    }

    private void Window_Closing(object? sender, CancelEventArgs e)
    {
        if (!_isExiting && _settings.MinimizeToTrayOnClose)
        {
            e.Cancel = true;
            Hide();
            return;
        }

        if (Browser.CoreWebView2 is not null && _browserPermissionsConfigured)
        {
            Browser.CoreWebView2.PermissionRequested -= Browser_PermissionRequested;
            Browser.CoreWebView2.WebMessageReceived -= Browser_AssistantState;
        }

        _assistantWindow?.CloseForExit();
        _assistantWindow = null;
        _assistantBall?.Close();
        _assistantBall = null;
        _pollTimer.Stop();
        SystemEvents.PowerModeChanged -= SystemEvents_PowerModeChanged;
        NetworkChange.NetworkAvailabilityChanged -= NetworkChange_NetworkAvailabilityChanged;
        _recoveryCts?.Cancel();
        _recoveryCts?.Dispose();
        _streamCts?.Cancel();
        _streamCts?.Dispose();
        _trayIcon?.Dispose();
        _apiClient.Dispose();
    }

    private async void RefreshButton_Click(object sender, RoutedEventArgs e)
    {
        await PollNotificationsAsync(showInitialSummary: true);
    }

    private async void ReloginButton_Click(object sender, RoutedEventArgs e)
    {
        await ReloginAsync();
    }

    private void SettingsButton_Click(object sender, RoutedEventArgs e)
    {
        OpenSettingsWindow();
    }

    private void OpenTodoButton_Click(object sender, RoutedEventArgs e)
    {
        OpenTodoPage();
    }

    private async Task ReloginAsync()
    {
        var oldServerOrigin = _apiClient.ServerUri.GetLeftPart(UriPartial.Authority);
        var login = new LoginWindow(_settings)
        {
            Owner = this,
        };
        if (login.ShowDialog() != true || login.ApiClient is null || login.Settings is null)
        {
            return;
        }

        var newServerOrigin = login.ApiClient.ServerUri.GetLeftPart(UriPartial.Authority);
        if (!oldServerOrigin.Equals(newServerOrigin, StringComparison.OrdinalIgnoreCase))
        {
            login.ApiClient.Dispose();
            System.Windows.MessageBox.Show(
                "服务器地址已变更，客户端将自动重启以重新加载相机权限。",
                "仓库物料系统客户端",
                MessageBoxButton.OK,
                MessageBoxImage.Information);
            RestartClient();
            return;
        }

        _pollTimer.Stop();
        _streamCts?.Cancel();
        _assistantWindow?.CloseForExit();
        _assistantWindow = null;
        _apiClient.Dispose();
        _apiClient = login.ApiClient;
        _settings = login.Settings;
        _seenNotifications.Clear();
        _seenSystemNotifications.Clear();
        _isFirstPoll = true;
        _isFirstSystemNotificationPoll = true;
        ServerText.Text = _apiClient.ServerUri.ToString().TrimEnd('/');
        await ApplyCookiesToWebViewAsync();
        Browser.CoreWebView2?.Navigate(_apiClient.ServerUri.ToString());
        await PollNotificationsAsync(showInitialSummary: true);
        StartRealtimeNotifications();
        _pollTimer.Start();
    }

    private void RestartClient()
    {
        var exePath = Environment.ProcessPath;
        if (!string.IsNullOrWhiteSpace(exePath))
        {
            Process.Start(new ProcessStartInfo(exePath) { UseShellExecute = true });
        }

        _isExiting = true;
        Close();
    }

    private void OpenTodoPage()
    {
        ShowMainWindow();
        var todoUri = new Uri(_apiClient.ServerUri, "/?view=todo");
        Browser.CoreWebView2?.Navigate(todoUri.ToString());
    }

    private void OpenNotificationsPage()
    {
        ShowMainWindow();
        var notificationUri = new Uri(_apiClient.ServerUri, "/?view=notifications");
        Browser.CoreWebView2?.Navigate(notificationUri.ToString());
    }

    private void OpenSettingsWindow()
    {
        ShowMainWindow();
        var settingsWindow = new SettingsWindow(_settings)
        {
            Owner = this,
        };
        if (settingsWindow.ShowDialog() == true)
        {
            _settings = settingsWindow.Settings;
            _pollTimer.Interval = TimeSpan.FromSeconds(Math.Max(15, _settings.PollSeconds));
        }
    }

}

