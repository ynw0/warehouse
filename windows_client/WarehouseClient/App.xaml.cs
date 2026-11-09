using System.Windows;

namespace WarehouseClient;

public partial class App : System.Windows.Application
{
    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        ShutdownMode = ShutdownMode.OnExplicitShutdown;

        try
        {
            var settings = SettingsService.Load();
            StartupService.SetEnabled(settings.StartWithWindows);
            if (string.IsNullOrWhiteSpace(settings.ServerUrl))
            {
                var server = new ServerWindow("http://192.168.21.4:5000");
                if (server.ShowDialog() != true)
                {
                    Shutdown();
                    return;
                }

                settings.ServerUrl = server.ServerUrl;
                SettingsService.Save(settings);
            }

            ApiClient? apiClient = null;
            if (settings.AutoLogin && settings.SavePassword && !string.IsNullOrWhiteSpace(settings.Username))
            {
                try
                {
                    var password = PasswordProtector.Unprotect(settings.EncryptedPassword);
                    if (!string.IsNullOrWhiteSpace(password))
                    {
                        apiClient = new ApiClient(settings.ServerUrl);
                        await apiClient.LoginAsync(settings.Username, password);
                    }
                }
                catch
                {
                    apiClient?.Dispose();
                    apiClient = null;
                }
            }

            if (apiClient is null)
            {
                var login = new LoginWindow(settings);
                if (login.ShowDialog() != true || login.ApiClient is null || login.Settings is null)
                {
                    Shutdown();
                    return;
                }

                apiClient = login.ApiClient;
                settings = login.Settings;
            }

            var main = new MainWindow(apiClient, settings);
            MainWindow = main;
            main.Show();
            main.WindowState = WindowState.Normal;
            main.Activate();
            ShutdownMode = ShutdownMode.OnMainWindowClose;

            try
            {
                await main.InitializeAsync();
            }
            catch (Exception ex)
            {
                System.Windows.MessageBox.Show(
                    $"主界面初始化失败：\n{ex.Message}",
                    "仓库物料系统客户端",
                    MessageBoxButton.OK,
                    MessageBoxImage.Error);
            }
        }
        catch (Exception ex)
        {
            System.Windows.MessageBox.Show(
                $"客户端启动失败：\n{ex.Message}",
                "仓库物料系统客户端",
                MessageBoxButton.OK,
                MessageBoxImage.Error);
            Shutdown();
        }
    }
}
