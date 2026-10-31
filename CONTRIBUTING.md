# Contributing

需要 Windows x64 和 Python 3.11–3.13。源码与发布构建均不需要上传个人配置。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试使用模拟 EC 控制器，不安装或打开驱动。修改实际 EC 读写前，请先阅读 [协议记录](docs/ec-protocol.md) 和 [兼容性范围](docs/compatibility.md)。不要让 CI 调用 `ec_probe.py` 或实际控制 CLI。

提交应说明具体问题、最终行为、测试方式和硬件验证范围。新增机型需要独立验证记录，不接受仅添加型号白名单的兼容性声明。

发布前更新 `version.py`、`version_info.txt` 和 `CHANGELOG.md`，运行测试，再执行 `python build.py`。Release 附件使用 `dist/` 下的完整 ZIP 与 `.sha256`，不单独分发缺少 `_internal/` 的 EXE。

第三方 pystray 对应源码保存在 `third_party/sources/`。修改它时保留相应许可证并随发布提供实际使用的源码；重建方式见 `third_party/README.md`。
