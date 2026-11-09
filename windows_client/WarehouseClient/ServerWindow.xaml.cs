using System.Windows;
using System.Windows.Input;

namespace WarehouseClient;

public partial class ServerWindow : Window
{
    public ServerWindow(string serverUrl)
    {
        InitializeComponent();
        ServerBox.Text = serverUrl;
        Loaded += (_, _) =>
        {
            ServerBox.Focus();
            ServerBox.SelectAll();
        };
    }

    public string ServerUrl { get; private set; } = "";

    private async void ConfirmButton_Click(object sender, RoutedEventArgs e)
    {
        await ConfirmAsync();
    }

    private async void ServerBox_KeyDown(object sender, System.Windows.Input.KeyEventArgs e)
    {
        if (e.Key == Key.Enter)
        {
            await ConfirmAsync();
        }
    }

    private async Task ConfirmAsync()
    {
        ErrorText.Text = "";
        ConfirmButton.IsEnabled = false;
        ConfirmButton.Content = "连接中...";

        try
        {
            using var client = new ApiClient(ServerBox.Text);
            await client.CheckServerAsync();
            ServerUrl = client.ServerUri.ToString().TrimEnd('/');
            DialogResult = true;
        }
        catch (Exception ex)
        {
            ErrorText.Text = ex.Message;
        }
        finally
        {
            ConfirmButton.IsEnabled = true;
            ConfirmButton.Content = "确定";
        }
    }

    private void CancelButton_Click(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
    }
}
