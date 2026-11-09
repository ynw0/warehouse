using System.Windows;

namespace WarehouseClient;

public partial class SettingsWindow : Window
{
    private readonly AppSettings _settings;

    public SettingsWindow(AppSettings settings)
    {
        InitializeComponent();
        _settings = settings;
        StartWithWindowsBox.IsChecked = settings.StartWithWindows;
        MinimizeToTrayBox.IsChecked = settings.MinimizeToTrayOnClose;
        ShowTrayBalloonBox.IsChecked = settings.ShowTrayBalloon;
        ShowDesktopToastBox.IsChecked = settings.ShowDesktopToast;
        PollSecondsBox.Text = settings.PollSeconds.ToString();
    }

    public AppSettings Settings => _settings;

    private void CancelButton_Click(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
    }

    private void SaveButton_Click(object sender, RoutedEventArgs e)
    {
        if (!int.TryParse(PollSecondsBox.Text.Trim(), out var seconds))
        {
            ErrorText.Text = "请输入有效的秒数。";
            return;
        }

        _settings.PollSeconds = Math.Clamp(seconds, 15, 600);
        _settings.StartWithWindows = StartWithWindowsBox.IsChecked == true;
        _settings.MinimizeToTrayOnClose = MinimizeToTrayBox.IsChecked == true;
        _settings.ShowTrayBalloon = ShowTrayBalloonBox.IsChecked == true;
        _settings.ShowDesktopToast = ShowDesktopToastBox.IsChecked == true;
        SettingsService.Save(_settings);
        StartupService.SetEnabled(_settings.StartWithWindows);
        DialogResult = true;
    }
}
