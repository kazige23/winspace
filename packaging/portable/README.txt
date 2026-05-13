winspace 绿色版 / Portable Edition
==================================

【是什么】
winspace 是一个 Windows C 盘空间释放工具。它通过 NTFS junction
把占地的目录(node_modules、浏览器缓存、包管理器缓存等)透明地
搬到其他盘符,原路径下的程序无感继续工作,也可以彻底删除缓存
直接释放空间。

无需安装。解压到任意位置,双击 winspace.exe 即可使用。

【使用方法】
1. 双击 winspace.exe                  打开图形界面(默认入口,无控制台)
2. cmd 中运行 winspace-cli.exe scan   CLI 模式扫描
3. cmd 中运行 winspace-cli.exe move   CLI 模式移动
4. cmd 中运行 winspace-cli.exe clean  CLI 模式删除
5. cmd 中运行 winspace-cli.exe undo   撤销最近的 move 操作

(两个 exe 都在文件夹里。普通用户只用 winspace.exe,
 想跑命令行的开发者用 winspace-cli.exe。)

【图形界面操作】
1. 点击右上"扫描"按钮 → 程序遍历常见可清理目录
2. 在列表中勾选你想处理的项
3. 顶部下拉框选目标盘(默认 D:)
4. 点"移动选中"或"删除选中"
5. 弹窗确认 → 执行

【安全机制】
* 黑名单:Windows 系统目录、Program Files、回收站、System Volume
  Information、hiberfil.sys / pagefile.sys 等永远拒绝操作
* 云同步保护:自动识别 OneDrive、iCloud、Google Drive、Dropbox、
  坚果云、百度网盘并拒绝触碰,防止云端误删
* IM 数据保护:微信、QQ、Discord、Telegram 等默认隐藏,GUI 中
  也不允许"删除"
* 每次移动都写入 manifest(%APPDATA%\winspace\manifest.json),
  支持随时撤销
* 移动流程"先复制+校验+建 junction+最后删源",任意环节失败
  数据都不会丢失

【常见问题】
Q: 提示 Defender 拦截?
A: 这是 PyInstaller 打包的 Python 程序常见的误报。把 winspace.exe
   加入排除项,或在 Defender 通知中点"允许"。

Q: 双击 winspace.exe 时为什么没有黑色控制台框?
A: 它使用 Windows 图形子系统启动,不开控制台。如果你想从 cmd 跑
   命令行版本,用同一文件夹里的 winspace-cli.exe。

Q: 数据真的不会丢吗?
A: 移动操作有 9 步反向保护流程,代码里 290+ 单元/集成测试覆盖了
   每一种失败场景。但任何系统级工具都建议先在不重要的目录上试
   一次,确认效果再操作重要数据。

【撤销 / 误操作恢复】
* GUI: 点"撤销最近"按钮
* CLI: winspace undo --last --yes

【源码 / 反馈】
https://github.com/kazige23/winspace
版本: v0.1.0
许可: MIT
