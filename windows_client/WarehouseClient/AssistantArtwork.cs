using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace WarehouseClient;

internal static class AssistantArtwork
{
    private static readonly Dictionary<string, BitmapSource> Cache = new();

    public static BitmapSource Load(string name)
    {
        if (Cache.TryGetValue(name, out var found)) return found;
        var image = new BitmapImage(new Uri("pack://application:,,,/WarehouseClient;component/Assets/Assistant/" + name + ".png"));
        var converted = new FormatConvertedBitmap(image, PixelFormats.Bgra32, null, 0);
        int width = converted.PixelWidth, height = converted.PixelHeight;
        int stride = width * 4;
        var pixels = new byte[stride * height];
        converted.CopyPixels(pixels, stride, 0);
        var visited = new bool[width * height];
        var queue = new Queue<int>();
        bool IsBackground(int index)
        {
            int p = index * 4;
            return pixels[p + 3] == 0 || (pixels[p] >= 240 && pixels[p + 1] >= 240 && pixels[p + 2] >= 240);
        }
        void Add(int index)
        {
            if (visited[index] || !IsBackground(index)) return;
            visited[index] = true;
            queue.Enqueue(index);
        }
        // 只移除与画布边缘相连的白底，保留角色内部的白色衣服与高光。
        for (int x = 0; x < width; x++) { Add(x); Add((height - 1) * width + x); }
        for (int y = 0; y < height; y++) { Add(y * width); Add(y * width + width - 1); }
        while (queue.Count > 0)
        {
            int index = queue.Dequeue();
            pixels[index * 4 + 3] = 0;
            int x = index % width, y = index / width;
            if (x > 0) Add(index - 1);
            if (x + 1 < width) Add(index + 1);
            if (y > 0) Add(index - width);
            if (y + 1 < height) Add(index + width);
        }
        int left = width, top = height, right = 0, bottom = 0;
        for (int y = 0; y < height; y++)
        for (int x = 0; x < width; x++)
        {
            if (pixels[y * stride + x * 4 + 3] == 0) continue;
            left = Math.Min(left, x); right = Math.Max(right, x);
            top = Math.Min(top, y); bottom = Math.Max(bottom, y);
        }
        var bitmap = BitmapSource.Create(width, height, 96, 96, PixelFormats.Bgra32, null, pixels, stride);
        BitmapSource result = new CroppedBitmap(bitmap, new System.Windows.Int32Rect(left, top, right - left + 1, bottom - top + 1));
        // 右侧探身素材的切边在左边，显示时镜像，让切边贴住屏幕右缘。
        if (name == "edge-right") result = new TransformedBitmap(result, new ScaleTransform(-1, 1));
        result.Freeze();
        Cache[name] = result;
        return result;
    }
}
