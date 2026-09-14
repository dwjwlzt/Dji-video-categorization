# DJI Action5 Pro 视频归档工具

一个基于 Python 和 CustomTkinter 的桌面工具，用于将 DJI Action5 Pro 拍摄的视频自动归档到目标目录。当前最新版本为 **v2.1 新 UI**。

## 功能

- 扫描 DJI 相机的视频目录
- 按拍摄日期和片段序号自动归档
- 分块复制大文件，并显示实时进度与速度
- 支持普通视频和延时摄影素材
- 可跳过已存在的目标文件
- 支持取消正在执行的任务
- v2.1 使用现代化红色主题 UI

## 主要文件

- `视频归类v2.1新ui.py`：当前最新版源代码
- `dji.ico`、`dji.png`：程序图标和界面资源
- `视频归类v2.0.py`、`视频归类.py` 等：历史版本源代码

> 默认源目录、目标目录和文件名规则写在 `视频归类v2.1新ui.py` 的配置常量中，可根据实际磁盘路径修改。

## 运行环境

- Windows 10/11
- Python 3.10 及以上
- `customtkinter`

安装依赖：

```powershell
python -m pip install -r requirements.txt
```

运行：

```powershell
python "视频归类v2.1新ui.py"
```

## 打包

项目不提交 PyInstaller 的 `.spec` 文件，直接使用以下命令打包：

```powershell
pyinstaller --noconfirm --onefile --windowed --icon "dji.ico" --name "视频归类v2.1新ui" --add-data "dji.ico;." --add-data "dji.png;." "视频归类v2.1新ui.py"
```

打包结果和 PyInstaller 生成的 `.spec` 文件都会被忽略。

## 说明

`build/`、`dist/`、`*.spec` 和 `__pycache__/` 等生成文件不会提交到 Git 仓库。
