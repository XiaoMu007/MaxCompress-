## v2.6.16 启动时更新公告

- 软件主窗口显示后自动检查 GitHub 最新正式版（后台线程，不阻塞启动）。
- 仅检测到新版本时自动弹出更新公告，展示版本号、Release Notes 和下载按钮；每次启动最多自动弹出一次。
- 无新版本或网络失败时不弹框，保留「检查更新」手动入口。
- 本版本继续基于 v2.6.x 分支，不包含 v2.7.x 云端授权。

# MaxCompress 使用说明

> **v2.6.10 新增：复制输出路径按钮；用于检验 v2.6.9 → v2.6.10 的完整自动安装流程。** 详见 [升级测试步骤](V2.6.10_升级测试步骤.md)。

> 更新中心会自动展示 GitHub 最新正式版的更新说明。

# MaxCompress

**当前源码版本：v2.6.16**

> 本版重点优化 UI 下拉框和任务列表易读性；具体改动与验证见 [v2.6.7 优化需求文档](V2.6.7_优化需求文档.md)。 · Windows 10/11（64 位）文件压缩桌面工具。

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
4. 编译后查看 `installer_output/MaxCompress_Setup_v2.6.16.exe`。安装包应包含所需运行时文件，实际产物请按干净系统测试。

如果 Inno Setup 提示找不到中文语言包，请核对安装目录的 `Languages/ChineseSimplified.isl`；必要时在 `installer.iss` 的 `[Languages]` 节只保留 `compiler:Default.isl` 英文配置。

## 发布升级

发布正式 `v2.6.16` GitHub Release 时，**Tag、`updater.py` 的 `VERSION`、`installer.iss` 的 `AppVersion` 与安装包文件名需一致**。上传 `installer_output` 中的 Setup EXE，而不是 `dist/` 目录下的可执行文件。详见 [发布指南](GITHUB_RELEASE_GUIDE.md)。

历史更新见 [CHANGELOG.md](CHANGELOG.md)。

## 使用提示

- 压缩任务并发数越大不一定越快，建议视频先测试 2～3 个并发。
- JPG、MP4 等已压缩格式的 ZIP 归档通常不会进一步明显减小体积；项目优先使用较快归档方式。
- GPU 编码可能更快，也可能增大文件大小；并非所有驱动或 FFmpeg 构建都支持对应编码器。
- 部分极限压缩会降低画质；重要素材请备份原文件。


## 稳定安装
参见 [STABLE_INSTALL_GUIDE.md](STABLE_INSTALL_GUIDE.md)。

### v2.6.12 输出格式
压缩界面新增按文件类别切换的“输出格式”下拉框，默认“保持原格式”。图片可指定 JPEG、PNG、WebP；视频可指定 MP4、MKV、MOV、WebM。PDF/Word/PPT 仅提供原格式。

### 更新安装
更新启动器在后台运行（不出现 PowerShell 控制台）；Windows 安装向导应正常显示。故障日志：`%LOCALAPPDATA%\MaxCompress\updates\installer_launch.log`。


## v2.6.18 新增
- ZIP 自动打包可选；取消勾选不会生成 ZIP。
- 文件数、总大小、已完成、失败和节省空间统计。
- 暂停请求与已暂停状态分别显示。
- 压缩失败时对新生成的同名输出文件做有限清理。
- 文件名列自适应宽度，减少横向滚动；原有每行状态和进度条保留。
