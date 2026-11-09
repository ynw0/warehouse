# WarehouseClient Windows 客户端

这是仓库物料系统的 Windows WPF 客户端壳。

## 功能

- 首次启动先配置服务器地址，再输入账号密码。
- 登录成功后内嵌 WebView2 打开仓库系统页面。
- 后台常驻 Windows 托盘。
- 关闭窗口时默认最小化到托盘，不退出程序。
- 优先通过 `/api/todos/stream` 接收 SSE 实时待办推送。
- SSE 断线时会自动重连，设置里的轮询间隔只作为兜底检查，默认 120 秒；电脑休眠恢复或网络恢复后会主动重连并刷新页面。
- 有新待办时显示 Windows 通知栏提示和桌面右上角自定义通知窗，自定义通知窗会一直显示到用户点击后关闭。
- 可选择保存密码，密码使用 Windows 当前用户 DPAPI 加密。
- 可选择自动登录。
- 可在客户端设置里开启开机自启、调整兜底检查间隔、开关通知方式。
- 安装新版本前会自动检查并停止旧客户端，调用旧版卸载程序清理旧文件，然后安装新版本；服务器地址等客户端设置会尽量保留。
- 拍照权限按当前配置的服务器地址动态启用，`192.168.21.4:5000` 只是首次启动默认值，不限制生产服务器地址。

## 安装

运行安装包：

```text
WarehouseClientInstaller.exe
```

安装器会把客户端安装到：

```text
%LocalAppData%\WarehouseClient
```

并在桌面生成快捷方式。

## 直接运行

```text
WarehouseClient.exe
```

首次启动默认服务器地址：



```text

http://192.168.21.4:5000

```



服务器地址示例：

```text
http://localhost:5000
http://192.168.1.20:5000
```

## 运行环境

发布目录 `publish/WarehouseClient-win-x64` 是自包含版本，不需要额外安装 .NET Desktop Runtime。

仍需要 Windows 上有 WebView2 Runtime。Windows 10/11 通常已经随 Edge 安装。
