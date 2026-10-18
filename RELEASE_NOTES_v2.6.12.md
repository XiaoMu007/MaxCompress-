# MaxCompress v2.6.12 更新说明

## 新增：输出格式下拉框
- 根据当前左侧分类动态显示格式：图片（保持原格式、JPEG、PNG、WEBP）、视频（保持原格式、MP4、MKV、MOV、WEBM）；PDF/Word/PPT 仅保持原格式。
- 默认“保持原格式”；图片输出保存原扩展名（支持 JPEG/PNG/WEBP/BMP/TIFF），视频原格式沿用受支持扩展名（MP4/MKV/MOV/WEBM/AVI/M4V）。
- 同一批次如包含其他类型，格式指定仅作用于当前分类，其余类型保持原格式。
- WEBM 使用 VP9 + Opus 以符合容器要求，编码通常较慢且 GPU H.264 不适用。
- 图片 PNG 与 WebP 在可能时保留透明通道；转换 JPEG 时透明区填充白色。

## 优化：安装启动体验
- Windows 更新启动脚本使用后台 PowerShell，不再显示控制台黑窗口。
- 仍保留本机安装器正常向导、错误提示窗口与安装日志。
- 日志位置：`%LOCALAPPDATA%\MaxCompress\updates\installer_launch.log`。

## 注意事项
- 仅修改源码无法更新已安装 EXE，须使用本版源码重新构建并安装。
- 本环境验证 Python 语法与 Pillow 图片格式实际保存；视频各容器与 Windows 更新安装流程仍需 Windows 实机验证。
