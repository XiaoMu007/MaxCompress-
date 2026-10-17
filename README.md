# MaxCompress

**当前源码版本：v2.6.2** · Windows 10/11（64 位）文件压缩桌面工具。

支持图片、视频、PDF、Word、PPT 的批量压缩。具有多任务并行、视频硬件编码自动尝试、图片/视频预览缩略图、实时进度显示、结果文件夹及 ZIP 自动打包、压缩失败日志和 GitHub Releases 更新检查等功能。

> 本仓库存放源码与 Windows 构建脚本，不是可直接运行的安装包。普通用户请到 [Releases](https://github.com/XiaoMu007/MaxCompress-/releases/latest) 下载 `MaxCompress_Setup_v*.exe`。软件压缩率、GPU 可用性和实际速度依赖设备、文件格式及 FFmpeg 环境。

## 主要特性

- **文件预览**：图片显示缩略图；视频通过 FFmpeg 提取首帧；PDF、Word、PPT 使用分类图标。
- **批量并行**：可选择并发任务数量；每个任务显示状态与进度。视频进度依据 FFmpeg 报告更新。
- **智能视频编码**：尝试 NVIDIA NVENC、Intel Quick Sync、AMD AMF 等支持的编码器；失败时可回退 CPU（libx264）；运行时显示实际使用的编码方式。
- **自动输出**：每批创建结果文件夹，并产生同名 ZIP 归档；默认保留原文件。
- **故障诊断**：任务失败时在结果目录写入 `压缩失败日志.txt`，记录错误信息。
- **安全操作**：移除任务前弹出“是/否”确认；该操作不删除磁盘原文件。
- **软件更新**：支持启动时及手动检查 GitHub 最新正式 Release，并提示下载安装。

## 开发者：在 Windows 上制作安装包

1. 获取本仓库的**完整文件夹**，放置到路径尽量简单的目录，如 `D:\MaxCompress`。
2. 安装 Python 3，并确保命令行可调用；项目依赖见 `requirements.txt`。
3. 双击 `build_installer.bat`，按照脚本提示安装/准备构建所需环境；首次会获取 FFmpeg（需要网络）。
4. 编译后查看 `installer_output/MaxCompress_Setup_v2.6.2.exe`。安装包应包含所需运行时文件，实际产物请按干净系统测试。

如果 Inno Setup 提示找不到中文语言包，请核对安装目录的 `Languages/ChineseSimplified.isl`；必要时在 `installer.iss` 的 `[Languages]` 节只保留 `compiler:Default.isl` 英文配置。

## 发布升级

发布正式 `v2.6.2` GitHub Release 时，**Tag、`updater.py` 的 `VERSION`、`installer.iss` 的 `AppVersion` 与安装包文件名需一致**。上传 `installer_output` 中的 Setup EXE，而不是 `dist/` 目录下的可执行文件。详见 [发布指南](GITHUB_RELEASE_GUIDE.md)。

历史更新见 [CHANGELOG.md](CHANGELOG.md)。

## 使用提示

- 压缩任务并发数越大不一定越快，建议视频先测试 2～3 个并发。
- JPG、MP4 等已压缩格式的 ZIP 归档通常不会进一步明显减小体积；项目优先使用较快归档方式。
- GPU 编码可能更快，也可能增大文件大小；并非所有驱动或 FFmpeg 构建都支持对应编码器。
- 部分极限压缩会降低画质；重要素材请备份原文件。
