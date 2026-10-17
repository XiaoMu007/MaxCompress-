# MaxCompress v2.2.0（增加 GitHub 自动更新）

具体更新配置和发布步骤见 [UPDATE_GUIDE.md](UPDATE_GUIDE.md)。

# MaxCompress Windows 自动构建版（断点续传）

下载后**先全部解压**，再双击 `build_windows.bat`。需要 Windows 10/11 x64 与 Python 3.11/3.12（添加到 PATH）。首次构建会从清华、阿里云、官方 PyPI 依次尝试安装依赖，下载 FFmpeg 后使用 PyInstaller 生成 `dist\MaxCompress.exe`。

## FFmpeg 下载改进

- 项目目录已有 `ffmpeg.exe` 且有效时直接复用。
- Windows PATH 里有有效 FFmpeg 时复制到项目目录。
- 优先下载相对较小的 Gyan essentials ZIP，再尝试两个 GitHub 加速中转及官方上游。
- 每个下载源最多四次重试，支持 HTTP Range 断点续传；部分文件保留在 `.ffmpeg_cache`，**关闭后重新运行也能续传**。
- 如果服务器不支持 Range，自动改为从头下载该镜像；不会将不同镜像的二进制拼接。
- 校验下载字节数、压缩包内容，并调用 `ffmpeg -version` 验证。
- `ffmpeg_download.log` 记录下载错误；`build_error_log.txt` 记录构建阶段。

注：具体镜像速度和断点续传支持由服务器决定。部分动态 `latest` 链接可能变更文件版本，遇到校验问题可以删除 `.ffmpeg_cache` 中相应 `.part` 后重试。本包是 Windows 构建项目，并非现成的 EXE，需在 Windows 上打包。


## 修复：ZipFile 类型不可用（2026-10-17）
- Windows PowerShell 5.1 显式加载 System.IO.Compression.FileSystem。
- 若 `.ffmpeg_cache/gyan_essentials.part` 已下载完整且 ZIP 结构有效，直接解压复用，无需重新下载。
- 不完整文件仍可断点续传（服务器必须支持 HTTP Range）。
- 如旧版正在下载，请先关闭旧窗口；将原目录的 `.ffmpeg_cache` 复制到新版解压目录，然后运行 `build_windows.bat`。
