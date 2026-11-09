using System.Windows;
using System.Windows.Input;

namespace WarehouseClient;

public partial class LoginWindow : Window
{
    private readonly AppSettings _initialSettings;

    public LoginWindow(AppSettings settings)
    {
        InitializeComponent();
        _initialSettings = settings;
        ServerText.Text = $"服务器：{settings.ServerUrl}";
        UsernameBox.Text = settings.Username;
        SavePasswordBox.IsChecked = settings.SavePassword;
        AutoLoginBox.IsChecked = settings.AutoLogin;
        AutoLoginBox.IsEnabled = settings.SavePassword;
        if (settings.SavePassword)
        {
            PasswordBox.Password = PasswordProtector.Unprotect(settings.EncryptedPassword);
        }

        Loaded += (_, _) =>
        {
            if (string.IsNullOrWhiteSpace(UsernameBox.Text))
            {
                UsernameBox.Focus();
            }
            else
            {
                PasswordBox.Focus();
            }
        };
    }

    public ApiClient? ApiClient { get; private set; }
    public AppSettings? Settings { get; private set; }

    private async void LoginButton_Click(object sender, RoutedEventArgs e)
    {
        await LoginAsync();
    }

    private async void PasswordBox_KeyDown(object sender, System.Windows.Input.KeyEventArgs e)
    {
        if (e.Key == Key.Enter)
        {
            await LoginAsync();
        }
    }

    private async Task LoginAsync()
    {
        ErrorText.Text = "";
        LoginButton.IsEnabled = false;
        LoginButton.Content = "连接中...";

        try
        {
            var serverUrl = _initialSettings.ServerUrl;
            var username = UsernameBox.Text.Trim();
            var password = PasswordBox.Password;

            if (string.IsNullOrWhiteSpace(username) ||
                string.IsNullOrWhiteSpace(password))
            {
                throw new InvalidOperationException("账号和密码都需要填写。");
            }

            var client = new ApiClient(serverUrl);
            await client.LoginAsync(username, password);

            var settings = new AppSettings
            {
                ServerUrl = client.ServerUri.ToString().TrimEnd('/'),
                Username = username,
                SavePassword = SavePasswordBox.IsChecked == true,
                AutoLogin = SavePasswordBox.IsChecked == true && AutoLoginBox.IsChecked == true,
                EncryptedPassword = SavePasswordBox.IsChecked == true ? PasswordProtector.Protect(password) : "",
                PollSeconds = _initialSettings.PollSeconds,
                StartWithWindows = _initialSettings.StartWithWindows,
                MinimizeToTrayOnClose = _initialSettings.MinimizeToTrayOnClose,
                ShowTrayBalloon = _initialSettings.ShowTrayBalloon,
                ShowDesktopToast = _initialSettings.ShowDesktopToast,
            };
            SettingsService.Save(settings);
            StartupService.SetEnabled(settings.StartWithWindows);

            ApiClient = client;
            Settings = settings;
            DialogResult = true;
        }
        catch (Exception ex)
        {
            ErrorText.Text = ex.Message;
        }
        finally
        {
            LoginButton.IsEnabled = true;
            LoginButton.Content = "确定";
        }
    }

    private void SavePasswordBox_Changed(object sender, RoutedEventArgs e)
    {
        AutoLoginBox.IsEnabled = SavePasswordBox.IsChecked == true;
        if (SavePasswordBox.IsChecked != true)
        {
            AutoLoginBox.IsChecked = false;
        }
    }

    private void ChangeServerButton_Click(object sender, RoutedEventArgs e)
    {
        var serverWindow = new ServerWindow(_initialSettings.ServerUrl)
        {
            Owner = this,
        };
        if (serverWindow.ShowDialog() == true)
        {
            _initialSettings.ServerUrl = serverWindow.ServerUrl;
            ServerText.Text = $"服务器：{_initialSettings.ServerUrl}";
            SettingsService.Save(_initialSettings);
        }
    }
}
