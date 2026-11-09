using System.Windows;

namespace WarehouseClient;

public partial class AssistantNoticeWindow : Window
{
    private readonly Action? _clickAction;

    public AssistantNoticeWindow(string title, string body, Action? clickAction)
    {
        InitializeComponent();
        _clickAction = clickAction;
        TitleText.Text = title;
        BodyText.Text = body;
        OpenButton.Visibility = clickAction is null ? Visibility.Collapsed : Visibility.Visible;
    }

    private void CloseButton_Click(object sender, RoutedEventArgs e) => Close();

    private void OpenButton_Click(object sender, RoutedEventArgs e)
    {
        Close();
        _clickAction?.Invoke();
    }
}
