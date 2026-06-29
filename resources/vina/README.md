# Bundled Vina Resource Directory

此目录用于 V0.6 之后的 DockStart bundled AutoDock Vina 资源。

当前仓库默认只提交本 README，不提交真实 `vina.exe` 或 DLL 文件。

预期本地目录：

```text
resources/vina/
├─ README.md
├─ vina.exe              # 本地准备后可存在，但默认被 .gitignore 忽略
└─ *.dll                 # 如 Vina 发行包确实需要，也默认被 .gitignore 忽略
```

说明：

- `resources/vina/vina.exe` 是 V0.6 推荐的新 bundled Vina 路径；
- 旧版 `resources/tools/vina/vina.exe` 可作为兼容回退；
- 准备脚本只使用本地文件，不联网、不下载 Vina；
- 准备脚本默认只复制 `vina.exe`，不会自动复制同目录 DLL；如果确认来源是干净的 Vina 发行包且 DLL 必需，可显式传入 `--copy-dlls`；
- 可使用 `--dry-run` 只更新 manifest 元数据，不复制二进制；
- 打包前必须确认 AutoDock Vina license、来源、版本和 sha256；
- 不要把未经确认的二进制提交进 Git。

示例：

```powershell
python scripts/prepare_bundled_vina.py C:\Path\To\vina.exe --source-label "local-vina-1.2.7"
python scripts/prepare_bundled_vina.py C:\Path\To\vina.exe --copy-dlls
python scripts/prepare_bundled_vina.py C:\Path\To\vina.exe --dry-run
```
