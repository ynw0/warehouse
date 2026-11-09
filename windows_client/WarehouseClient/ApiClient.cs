using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace WarehouseClient;

public sealed class ApiClient : IDisposable
{
    private readonly CookieContainer _cookies = new();
    private readonly HttpClientHandler _handler;
    private readonly HttpClient _http;
    private readonly JsonSerializerOptions _jsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
    };

    public ApiClient(string serverUrl)
    {
        ServerUri = NormalizeServerUri(serverUrl);
        _handler = new HttpClientHandler
        {
            CookieContainer = _cookies,
            UseCookies = true,
            AllowAutoRedirect = false,
        };
        _http = new HttpClient(_handler)
        {
            BaseAddress = ServerUri,
            Timeout = TimeSpan.FromSeconds(15),
        };
    }

    public Uri ServerUri { get; }

    public CookieCollection Cookies => _cookies.GetCookies(ServerUri);

    public async Task CheckServerAsync(CancellationToken cancellationToken = default)
    {
        try
        {
            var response = await _http.GetAsync("/api/session", cancellationToken);
            if (!response.IsSuccessStatusCode)
            {
                throw new InvalidOperationException($"服务器连接失败：{(int)response.StatusCode} {response.ReasonPhrase}");
            }
        }
        catch (HttpRequestException ex)
        {
            throw new InvalidOperationException($"无法连接服务器：{ex.Message}", ex);
        }
        catch (TaskCanceledException ex)
        {
            throw new InvalidOperationException("连接服务器超时，请检查服务器地址和网络。", ex);
        }
    }

    public async Task LoginAsync(string username, string password, CancellationToken cancellationToken = default)
    {
        var response = await _http.PostAsJsonAsync("/api/login", new { username, password }, cancellationToken);
        if (!response.IsSuccessStatusCode)
        {
            var message = await ReadErrorAsync(response);
            throw new InvalidOperationException(message);
        }
    }

    public async Task<NotificationPayload> GetNotificationsAsync(CancellationToken cancellationToken = default)
    {
        var response = await _http.GetAsync("/api/todos", cancellationToken);
        if (response.StatusCode is HttpStatusCode.Unauthorized or HttpStatusCode.Redirect)
        {
            throw new UnauthorizedAccessException("登录已失效，请重新登录。");
        }

        response.EnsureSuccessStatusCode();
        return await response.Content.ReadFromJsonAsync<NotificationPayload>(_jsonOptions, cancellationToken) ?? new NotificationPayload();
    }

    public async Task<int> MarkAllNotificationsReadAsync(CancellationToken cancellationToken = default)
    {
        using var response = await _http.PostAsJsonAsync("/api/notifications/read-all", new { }, cancellationToken);
        if (response.StatusCode is HttpStatusCode.Unauthorized or HttpStatusCode.Redirect)
            throw new UnauthorizedAccessException("登录已失效，请重新登录。");
        response.EnsureSuccessStatusCode();
        var payload = await response.Content.ReadFromJsonAsync<ReadAllResult>(_jsonOptions, cancellationToken);
        return payload?.Updated ?? 0;
    }

    private sealed class ReadAllResult
    {
        [JsonPropertyName("updated")]
        public int Updated { get; set; }
    }

    public async Task StreamNotificationsAsync(
        Func<NotificationPayload, Task> onNotifications,
        CancellationToken cancellationToken = default)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, "/api/todos/stream");
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("text/event-stream"));

        using var response = await _http.SendAsync(
            request,
            HttpCompletionOption.ResponseHeadersRead,
            cancellationToken);

        if (response.StatusCode is HttpStatusCode.Unauthorized or HttpStatusCode.Redirect)
        {
            throw new UnauthorizedAccessException("登录已失效，请重新登录。");
        }

        response.EnsureSuccessStatusCode();
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
        using var reader = new StreamReader(stream, Encoding.UTF8);
        var data = new StringBuilder();

        while (!cancellationToken.IsCancellationRequested)
        {
            var line = await reader.ReadLineAsync(cancellationToken);
            if (line is null)
            {
                break;
            }

            if (line.Length == 0)
            {
                if (data.Length > 0)
                {
                    var payload = JsonSerializer.Deserialize<NotificationPayload>(data.ToString(), _jsonOptions);
                    if (payload is not null)
                    {
                        await onNotifications(payload);
                    }
                    data.Clear();
                }
                continue;
            }

            if (line.StartsWith("data:", StringComparison.OrdinalIgnoreCase))
            {
                if (data.Length > 0)
                {
                    data.Append('\n');
                }
                data.Append(line[5..].TrimStart());
            }
        }
    }

    private async Task<string> ReadErrorAsync(HttpResponseMessage response)
    {
        try
        {
            var payload = await response.Content.ReadFromJsonAsync<ApiError>(_jsonOptions);
            if (!string.IsNullOrWhiteSpace(payload?.Error))
            {
                return payload.Error;
            }
        }
        catch
        {
            // Fall through to generic message.
        }

        return $"请求失败：{(int)response.StatusCode} {response.ReasonPhrase}";
    }

    private static Uri NormalizeServerUri(string value)
    {
        var text = (value ?? "").Trim().TrimEnd('/');
        if (string.IsNullOrWhiteSpace(text))
        {
            throw new ArgumentException("服务器地址不能为空。");
        }

        if (!text.StartsWith("http://", StringComparison.OrdinalIgnoreCase) &&
            !text.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
        {
            text = "http://" + text;
        }

        return new Uri(text, UriKind.Absolute);
    }

    public void Dispose()
    {
        _http.Dispose();
        _handler.Dispose();
    }

    private sealed class ApiError
    {
        [JsonPropertyName("error")]
        public string Error { get; set; } = "";
    }
}
