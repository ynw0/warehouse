using System.Security.Cryptography;
using System.Text;

namespace WarehouseClient;

public static class PasswordProtector
{
    public static string Protect(string password)
    {
        if (string.IsNullOrEmpty(password))
        {
            return "";
        }

        var bytes = Encoding.UTF8.GetBytes(password);
        var protectedBytes = ProtectedData.Protect(bytes, null, DataProtectionScope.CurrentUser);
        return Convert.ToBase64String(protectedBytes);
    }

    public static string Unprotect(string encrypted)
    {
        if (string.IsNullOrWhiteSpace(encrypted))
        {
            return "";
        }

        try
        {
            var protectedBytes = Convert.FromBase64String(encrypted);
            var bytes = ProtectedData.Unprotect(protectedBytes, null, DataProtectionScope.CurrentUser);
            return Encoding.UTF8.GetString(bytes);
        }
        catch
        {
            return "";
        }
    }
}
