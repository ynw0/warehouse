using System.Windows;
namespace WarehouseClient;

public partial class ToastWindow : Window
{
    private readonly Action? _clickAction;

    public ToastWindow(string title, string body, Action? clickAction = null)
    {
        InitializeComponent();
        _clickAction = clickAction;
        TitleText.Text = title;
        BodyText.Text = body;
        Loaded += (_, _) => PositionTopRight();
        MouseLeftButtonUp += (_, _) => CloseFromUserClick();
    }

    private void PositionTopRight()
    {
        var area = SystemParameters.WorkArea;
        Left = area.Right - Width - 18;
        Top = area.Top + 18;
    }

    private void CloseFromUserClick()
    {
        Close();
        _clickAction?.Invoke();
    }
}
