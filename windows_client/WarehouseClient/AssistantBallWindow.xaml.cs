using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Threading;
using WinForms = System.Windows.Forms;

namespace WarehouseClient;

public partial class AssistantBallWindow : Window
{
    private readonly Action _openAssistant;
    private readonly Action _openSystem;
    private readonly Func<Task> _markRead;
    private readonly Queue<AssistantNotice> _notices = new();
    private AssistantNoticeWindow? _notice;
    private AssistantNotice? _currentNotice;
    private readonly DispatcherTimer _hideTimer = new() { Interval = TimeSpan.FromSeconds(4) };
    private readonly DispatcherTimer _visibilityTimer = new() { Interval = TimeSpan.FromMilliseconds(300) };
    private bool _suppressed;
    private bool _closing;
    private string _edge = "";
    private string _activity = "idle";
    private string _art = "";
    private System.Windows.Point _expandedPosition;
    private DateTime _lastInteraction = DateTime.Now;
    private readonly DispatcherTimer _resetTimer = new() { Interval = TimeSpan.FromSeconds(2) };
    private readonly DispatcherTimer _idleTimer = new() { Interval = TimeSpan.FromSeconds(8) };
    private bool _edgeReminder;
    private ContextMenu _menu = null!;

    public AssistantBallWindow(Action openAssistant, Action openSystem, Func<Task> markRead)
    {
        InitializeComponent();
        ShowArtwork("greeting");
        _resetTimer.Tick += (_, _) => { _resetTimer.Stop(); _activity = "idle"; RefreshArtwork(); };
        _idleTimer.Tick += (_, _) =>
        {
            if (!string.IsNullOrEmpty(_edge) || _activity != "idle" || _notice != null) return;
            if ((DateTime.Now - _lastInteraction).TotalMinutes >= 2) ShowArtwork("sleep");
            else { ShowArtwork("blink"); _resetTimer.Start(); }
        };
        _openAssistant = openAssistant;
        _openSystem = openSystem;
        _markRead = markRead;
        var menu = new ContextMenu();
        _menu = menu;
        var open = new MenuItem { Header = "打开小助手" };
        open.Click += (_, _) => _openAssistant();
        var system = new MenuItem { Header = "打开库房系统" };
        system.Click += (_, _) => _openSystem();
        var read = new MenuItem { Header = "一键已读" };
        read.Click += async (_, _) => await _markRead();
        menu.Items.Add(open);
        menu.Items.Add(system);
        menu.Items.Add(read);
        BallRoot.ContextMenu = menu;
        Loaded += (_, _) =>
        {
            var area = SystemParameters.WorkArea;
            Left = area.Right - Width;
            Top = area.Top + Math.Min(160, Math.Max(0, area.Height - Height));
            _hideTimer.Start();
            _resetTimer.Start();
            _idleTimer.Start();
            RefreshWorkVisibility();
            _visibilityTimer.Start();
        };
        _visibilityTimer.Tick += (_, _) => RefreshWorkVisibility();
        _hideTimer.Tick += (_, _) => { _hideTimer.Stop(); HideAtEdge(); };
        MouseEnter += (_, _) =>
        {
            _lastInteraction = DateTime.Now;
            _hideTimer.Stop();
            if (!_edgeReminder) Reveal();
            RefreshArtwork();
        };
        MouseLeave += (_, _) => _hideTimer.Start();
        BallRoot.MouseLeftButtonDown += (_, _) =>
        {
            Reveal();
            var start = new System.Windows.Point(Left, Top);
            DragMove();
            if (Math.Abs(Left - start.X) + Math.Abs(Top - start.Y) < 4)
            {
                _openAssistant();
                return;
            }
            var area = WorkArea();
            Left = Math.Clamp(Left, area.Left, Math.Max(area.Left, area.Right - Width));
            Top = Math.Clamp(Top, area.Top, Math.Max(area.Top, area.Bottom - Height));
            PositionNotice();
            _hideTimer.Start();
        };
    }

    public Rect CurrentWorkArea() => WorkArea();

    public bool RefreshWorkVisibility()
    {
        if (!IsLoaded || _closing) return _suppressed;
        var suppressed = AssistantWorkVisibility.ShouldHide(new WindowInteropHelper(this).Handle);
        if (suppressed == _suppressed) return suppressed;
        _suppressed = suppressed;
        if (suppressed)
        {
            // 只隐藏窗口，保留当前提醒及队列，不能将未查看的通知当作已读。
            _hideTimer.Stop();
            _menu.IsOpen = false;
            _notice?.Hide();
            Hide();
        }
        else
        {
            // ShowActivated=False 保证恢复形象和提醒时不切走用户焦点。
            Show();
            if (_notice != null) { PositionNotice(); _notice.Show(); }
            else ShowNextNotice();
            _hideTimer.Start();
        }
        return suppressed;
    }

    private Rect WorkArea()
    {
        var origin = PointToScreen(new System.Windows.Point(ActualWidth / 2, ActualHeight / 2));
        var screen = WinForms.Screen.FromPoint(new System.Drawing.Point((int)origin.X, (int)origin.Y));
        var transform = PresentationSource.FromVisual(this)?.CompositionTarget?.TransformFromDevice ?? Matrix.Identity;
        var topLeft = transform.Transform(new System.Windows.Point(screen.WorkingArea.Left, screen.WorkingArea.Top));
        var bottomRight = transform.Transform(new System.Windows.Point(screen.WorkingArea.Right, screen.WorkingArea.Bottom));
        return new Rect(topLeft, bottomRight);
    }

    private bool PointerInside()
    {
        var topLeft = PointToScreen(new System.Windows.Point(0, 0));
        var bottomRight = PointToScreen(new System.Windows.Point(ActualWidth, ActualHeight));
        var cursor = WinForms.Cursor.Position;
        return new Rect(topLeft, bottomRight).Contains(new System.Windows.Point(cursor.X, cursor.Y));
    }

    private void HideAtEdge()
    {
        if (!IsLoaded || _suppressed || PointerInside() || _menu.IsOpen || _notice != null) return;
        if (!string.IsNullOrEmpty(_edge))
        {
            if (_edgeReminder) CollapseAtEdge();
            return;
        }
        var area = WorkArea();
        var distances = new Dictionary<string, double>
        {
            ["left"] = Math.Abs(Left - area.Left), ["right"] = Math.Abs(Left + Width - area.Right),
            ["top"] = Math.Abs(Top - area.Top), ["bottom"] = Math.Abs(Top + Height - area.Bottom),
        };
        var nearest = distances.OrderBy(item => item.Value).First();
        if (nearest.Value > 24) return;
        _edge = nearest.Key;
        _expandedPosition = new System.Windows.Point(Left, Top);
        CollapseAtEdge();
    }

    private void ShowDockedReminder()
    {
        var area = WorkArea();
        _edgeReminder = true;
        Width = 50;
        Height = 65;
        Opacity = 0.95;
        ShowArtwork(_edge == "left" ? "edge-left" : "edge-right");
        Left = _edge == "left" ? area.Left : (_edge == "right" ? area.Right - Width : Math.Clamp(_expandedPosition.X, area.Left, Math.Max(area.Left, area.Right - Width)));
        Top = Math.Clamp(_expandedPosition.Y, area.Top, Math.Max(area.Top, area.Bottom - Height));
        PositionNotice();
    }

    private void CollapseAtEdge()
    {
        if (string.IsNullOrEmpty(_edge)) return;
        if (PointerInside() || _menu.IsOpen) return;
        _edgeReminder = false;
        var area = WorkArea();
        Width = 22;
        Height = 14;
        Opacity = 0.4;
        NoticeBadge.Visibility = Visibility.Collapsed;
        ShowArtwork(_edge == "left" ? "tuft-left" : "tuft-right");
        Left = _edge == "right" ? area.Right - Width : Math.Clamp(_expandedPosition.X, area.Left, Math.Max(area.Left, area.Right - Width));
        Top = _edge == "bottom" ? area.Bottom - Height : Math.Clamp(_expandedPosition.Y, area.Top, Math.Max(area.Top, area.Bottom - Height));
        if (_edge == "left") Left = area.Left;
        if (_edge == "top") Top = area.Top;
        PositionNotice();
    }

    private void Reveal()
    {
        if (string.IsNullOrEmpty(_edge)) return;
        _edgeReminder = false;
        var area = WorkArea();
        Width = 70;
        Height = 75;
        Opacity = 1;
        Left = Math.Clamp(_expandedPosition.X, area.Left, Math.Max(area.Left, area.Right - Width));
        Top = Math.Clamp(_expandedPosition.Y, area.Top, Math.Max(area.Top, area.Bottom - Height));
        _edge = "";
        RefreshArtwork();
        UpdateBadge();
        PositionNotice();
    }

    public void SetActivity(string activity)
    {
        if (!new[] { "idle", "busy", "thinking", "success", "error" }.Contains(activity)) return;
        _activity = activity;
        _lastInteraction = DateTime.Now;
        _resetTimer.Stop();
        if (activity is "success" or "error") _resetTimer.Start();
        RefreshArtwork();
    }

    private void RefreshArtwork()
    {
        if (!string.IsNullOrEmpty(_edge)) return;
        var state = _activity == "idle" && _notice != null ? "reminder" : _activity;
        ShowArtwork(state);
    }

    private void ShowArtwork(string state)
    {
        if (_art == state) return;
        _art = state;
        // 贴边状态按屏幕边缘对齐，避免等比缩放后留出横向空隙。
        var alignment = state is "edge-left" or "tuft-left" ? System.Windows.HorizontalAlignment.Left
            : state is "edge-right" or "tuft-right" ? System.Windows.HorizontalAlignment.Right
            : System.Windows.HorizontalAlignment.Center;
        PreviousImage.HorizontalAlignment = StateImage.HorizontalAlignment = alignment;
        PreviousImage.Source = StateImage.Source;
        StateImage.Source = AssistantArtwork.Load(state);
        var duration = TimeSpan.FromMilliseconds(180);
        PreviousImage.BeginAnimation(OpacityProperty, new DoubleAnimation(1, 0, duration));
        StateImage.BeginAnimation(OpacityProperty, new DoubleAnimation(0, 1, duration));
        StateScale.BeginAnimation(ScaleTransform.ScaleXProperty, new DoubleAnimation(0.97, 1, duration));
        StateScale.BeginAnimation(ScaleTransform.ScaleYProperty, new DoubleAnimation(0.97, 1, duration));
    }

    public void EnqueueNotice(string title, string body, Action? clickAction, bool isSystemNotice)
    {
        RefreshWorkVisibility();
        _notices.Enqueue(new AssistantNotice(title, body, clickAction, isSystemNotice));
        UpdateBadge();
        ShowNextNotice();
    }

    public void ClearSystemNotices()
    {
        var remaining = _notices.Where(item => !item.IsSystemNotice).ToArray();
        _notices.Clear();
        foreach (var item in remaining) _notices.Enqueue(item);
        if (_currentNotice?.IsSystemNotice == true) _notice?.Close();
        UpdateBadge();
    }

    private void ShowNextNotice()
    {
        if (_closing || _suppressed || _notice != null || _notices.Count == 0) return;
        if (string.IsNullOrEmpty(_edge)) Reveal();
        else ShowDockedReminder();
        _currentNotice = _notices.Dequeue();
        _notice = new AssistantNoticeWindow(_currentNotice.Title, _currentNotice.Body, _currentNotice.ClickAction);
        _notice.Closed += (_, _) =>
        {
            _notice = null;
            _currentNotice = null;
            UpdateBadge();
            ShowNextNotice();
            RefreshArtwork();
            _hideTimer.Start();
        };
        _notice.Show();
        RefreshArtwork();
        PositionNotice();
        UpdateBadge();
    }

    private void PositionNotice()
    {
        if (_notice == null || !IsLoaded) return;
        var area = WorkArea();
        var left = Left + Width + _notice.Width + 10 <= area.Right ? Left + Width + 8 : Left - _notice.Width - 8;
        _notice.Left = Math.Clamp(left, area.Left, Math.Max(area.Left, area.Right - _notice.Width));
        _notice.Top = Math.Clamp(Top, area.Top, Math.Max(area.Top, area.Bottom - _notice.Height));
    }

    private void UpdateBadge()
    {
        var count = _notices.Count + (_notice == null ? 0 : 1);
        NoticeBadge.Visibility = count > 0 ? Visibility.Visible : Visibility.Collapsed;
        BadgeText.Text = count > 99 ? "99+" : count.ToString();
    }

    protected override void OnClosed(EventArgs e)
    {
        _closing = true;
        _visibilityTimer.Stop();
        _hideTimer.Stop();
        _idleTimer.Stop();
        _resetTimer.Stop();
        _notices.Clear();
        _notice?.Close();
        base.OnClosed(e);
    }
}

public sealed record AssistantNotice(string Title, string Body, Action? ClickAction, bool IsSystemNotice);
