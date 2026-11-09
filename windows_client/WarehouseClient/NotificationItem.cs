using System.Text.Json.Serialization;

namespace WarehouseClient;

public sealed class NotificationPayload
{
    [JsonPropertyName("items")]
    public List<NotificationItem> Items { get; set; } = [];

    [JsonPropertyName("notifications")]
    public List<SystemNotificationItem> Notifications { get; set; } = [];
}

public sealed class NotificationItem
{
    [JsonPropertyName("form_id")]
    public int FormId { get; set; }

    [JsonPropertyName("form_no")]
    public string FormNo { get; set; } = "";

    [JsonPropertyName("form_type")]
    public string FormType { get; set; } = "";

    [JsonPropertyName("step_code")]
    public string StepCode { get; set; } = "";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "";

    [JsonPropertyName("updated_at")]
    public string UpdatedAt { get; set; } = "";

    [JsonPropertyName("notification_title")]
    public string NotificationTitle { get; set; } = "";

    [JsonPropertyName("notification_body")]
    public string NotificationBody { get; set; } = "";

    public string Key => $"{FormId}:{FormType}:{StepCode}:{Status}:{UpdatedAt}";
}

public sealed class SystemNotificationItem
{
    [JsonPropertyName("id")]
    public int Id { get; set; }

    [JsonPropertyName("title")]
    public string Title { get; set; } = "";

    [JsonPropertyName("body")]
    public string Body { get; set; } = "";

    [JsonPropertyName("created_at")]
    public string CreatedAt { get; set; } = "";

    [JsonPropertyName("is_read")]
    public bool IsRead { get; set; }

    public string Key => $"notice:{Id}:{CreatedAt}";
}
