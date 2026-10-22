# 差异文档

当前更新：[1.6.0实施说明](1.6.0/实施说明.md)。按用户确认保留1.5.23全部连接实现，只移植功能/UI及此前确认的四项补充。以下固定远程比较是历史来源，不代表本次移植连接章节。

本文件夹集中保存v1.5.23与2026-10-23核对的GitHub远程main之间的全部净差异材料。

| 项目 | 固定范围 |
| --- | --- |
| 基准提交 | `77e35bd7f71f260b468fb0e8bdfae7a699af93a2`（v1.5.23） |
| 远程提交 | `46b922abfcb11c744b6ee1501e8d3bf4df913a9c`（main，源码版本1.7.0） |
| 净差异 | 14个提交，25个文件 |
| 本次开发 | 1.7.1尚未发布，不混入上述历史比较 |

- [完整说明](v1.5.23-remote-comparison.md)：功能、UI、Codex CLI连接为并列章节；包含功能伪代码和完整原始HTML代码。
- [全部25个文件的完整Git差异](comparison-1.5.23-remote-20261023/all-files.diff)：包含源码、界面、脚本、测试、说明、聚合快照及快捷方式文件，没有排除或截断文件。
- [完整文件清单](comparison-1.5.23-remote-20261023/changed-files.tsv)：逐文件新增/删除行数。
- [完整UI差异](comparison-1.5.23-remote-20261023/ui-full.diff)：主页、帮助及图标原始代码的完整差异。
- [v1.5.23原始HTML目录](comparison-1.5.23-remote-20261023/v1.5.23/) 与 [远程原始HTML目录](comparison-1.5.23-remote-20261023/remote-main/)：独立文件与Git blob逐字节一致，保留注释与样式覆盖顺序。
- [文件校验值](comparison-1.5.23-remote-20261023/SHA256.md)、[Python/DOM/CSS复核](comparison-1.5.23-remote-20261023/semantic-audit.json)、[JavaScript复核](comparison-1.5.23-remote-20261023/javascript-audit.json)。

`all-files.diff`中的中间历史、调查记录和仓库聚合文本不是独立产品功能；净功能结论以完整说明中标明的触发条件和代码分支为准。原始HTML中的会话占位符保持原样，直接打开源码文件不等于运行后台。

2026-10-23已发布1.6.0-pre.1：[发布分支说明](1.6.0/发布分支说明.md)、[预发布更新日志](1.6.0/1.6.0-pre.1-更新日志.md)、[远程与安装核验](1.6.0/预发布核验.json)。正式Latest保持1.5.23。
