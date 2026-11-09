namespace WarehouseClient;

public sealed class AppSettings
{
    public string ServerUrl { get; set; } = "http://192.168.21.4:5000";
    public string Username { get; set; } = "";
    public string EncryptedPassword { get; set; } = "";
    public bool SavePassword { get; set; }
    public bool AutoLogin { get; set; }
    public bool StartWithWindows { get; set; } = true;
    public bool MinimizeToTrayOnClose { get; set; } = true;
    public bool ShowTrayBalloon { get; set; } = true;
    public bool ShowDesktopToast { get; set; } = true;
    public int PollSeconds { get; set; } = 120;
}
