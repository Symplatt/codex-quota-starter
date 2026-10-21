# 待解决问题

## 1.7.0 远程发布完成（2026-10-21）

- 用户已明确批准“Push and release”；代码提交`7e056740d26d959d64af2849edfdb78e4e809da6`已推送至origin/main，v1.7.0 Tag指向同一发布代码。
- 正式Release：https://github.com/Symplatt/codex-quota-starter/releases/tag/v1.7.0；非草稿、非预发布，内容与CHANGELOG一致，包含Windows ZIP及SHA256SUMS.txt。
- main与v1.7.0的Windows CI均成功：39551328081、39551328329，Python/Node回归及构建通过。
- 发布资产重新下载核验成功；ZIP SHA256为`dbdf6e48584df306d44edb79d65a97177a7ce11e43504a2cde83a6ffbfea37f5`，与本地dist一致，25个已安装文件与该ZIP一致；安装版1.7.0，数据库完整性ok，当前真实连接正常。
- 本次更新日志与UI自行调整说明在D盘；ZIP未放D盘。代理间歇超时仍保留此前验证限制，不将发布完成等同于已根治所有网络故障。
- 下文审核/未发布说明是发布前的历史记录；本节为最新交付状态。本发布记录单独提交，不修改发布代码、Tag或资产。


## 1.7.0 本地更新与审核状态

- 最新状态（2026-10-21 13:41，北京时间）：追加断联处理已覆盖安装到1.7.0，真实读取恢复；165项Python、40项Node回归通过。暂不宣称间歇断联已经根治，见文末本次安装结论。Push与Release仍等待审核。

- 已完成：全部界面修改、设置开关、统一JSONL及旧归档校验迁移、代理选择与有界重连处理、版本及说明更新。
- 已验证：156项Python、40项Node回归；真实后台连接成功，页面及390px窄窗口检查；日志完整占据一屏，三列首行对齐，节点启用/禁用均保持不透明，悬浮标签分两行且百分点取整数。
- 已保留：27条发送、49条防重复记录，数据库完整性ok，以及每次安装前的最新计划、暂停和暂停检测偏好；开机启动设置与桌面快捷方式。旧04:30计划仅是20日安装时状态，不可用来覆盖21日用户修改。
- 本地交付：覆盖安装1.7.0；Windows ZIP及校验文件保留dist，更新日志及简明UI调整说明交付D盘，本次及后续ZIP不放D盘；实际预览保存在项目外quota-ui-review目录。
- 连接限制：本机7897代理链路已确认，启动即注入明确代理、优先npm CLI并减少重复冷启动；仍观察到上游路由发现/额度HTTP间歇超时，不宣称长期无断线。原5次/60秒及20分钟恢复规则保留。
- 待用户统一审核：已创建本地commit和Tag；只有Push（含Tag推送）与GitHub Release等待明确通过；尚未远程发布，不作正式发布完成声明。本次例外不延续到其他更新。

#### 断联调查交接（2026-10-21，继续处理中）

#### 当前源码、安装与授权

- 用户明确要求继续解决断联；2026-10-21明确选择“接到 master 的 1.7.0 更新”。已从main切回master，main的用户UI备份不覆盖、不合并。
- master当前基线：`e34ef088fcaf440b8dd1153fdd0b281e523fd445`（用户提交“改到一半先备份”）；包含下述尚未安装的恢复逻辑修改。main的`45972b5`是另一份UI修改备份。
- 前一安装交付代码为`e28c1106d7d4c692cb80de994f683eb0378f4aa4`；本地v1.7.0 Tag仍指向它。断联追加修改完成后需更新本地commit/Tag/发布包及安装，不能把旧包视作最新修复。
- 实际安装目录：`C:\Users\HP\AppData\Local\Programs\QuotaStarter`；服务`http://127.0.0.1:8770/`，已安装版本1.7.0。源码目录`E:\work-space\quota-starter`。
- 本次全部开发、验证、覆盖安装、本地commit/Tag、D盘文档须先完成；Push（包括Tag）与GitHub Release等待用户统一审核。没有批准远程发布。
- 本次及今后ZIP均不放D盘。构建包留在dist；D盘保留更新日志及UI自行调整说明。
- 不为诊断额外发送模型请求；只读账户与额度，避免读取或复制认证文件。保留用户最新计划、暂停、暂停检测、自启及防重复记录。
- 必须保持原策略：正常完成后60秒；失败间隔10秒，一轮最多5次/60秒；耗尽后每20分钟单次恢复；发送结果未知仍停止保护。

#### 已确认的真实失败模式

- 10月20日21:51、22:04、22:09、22:10、22:19等多次额度接口失败：`account/rateLimits/read`返回`failed to fetch codex rate limits: error sending request for url (https://chatgpt.com/backend-api/wham/usage)`。
- 请求通常约15.4–15.9秒失败；`processAlive=true`，明确代理为`http://127.0.0.1:7897`。不是每次都未走代理，也不是Python后台退出。
- 官方CLI独立只读诊断同样复现：22:16–22:17共4轮，1为`account/read: workspace routing discovery timed out`（15秒）、2成功8.375秒、3额度读取失败15.484秒、4成功0.875秒。
- reqwest连接日志确认首次连接就进入7897（连接本机端口仅毫秒），随后才失败；与应用页面/统计渲染无关。
- 无凭据curl对照：HTTP代理同端口TLS握手失败，15.616685秒，`schannel: failed to receive handshake`；紧接SOCKS5h同端口取得预期401，TLS3.342629秒、总3.531646秒。只各一次，不能据此断言改SOCKS就能根治，仍可能是随机目标IP或出口差异。
- 最后20日22:20:21日志：第二次额度错误后重建CLI，第三次account/read仅剩8.047秒预算，被本地总预算截断；然后安排22:40:21恢复。
- 21日02:23:58至02:27:01安装版连续4个正常分钟采样（0.8–0.9秒），说明故障间歇存在；短时恢复不代表根因消失。

#### 代理侧的关键线索（尚未修改代理配置）

- 7897由Clash Verge的`verge-mihomo.exe`监听；调查时PID15264，实际mihomo版本v1.19.32。后续必须重新确认PID和管道。
- 代理配置根目录：`C:\Users\HP\AppData\Roaming\io.github.clash-verge-rev.clash-verge-rev`。
- `clash-verge.yaml`中的controller pipe含`sidecar-release`，但真实运行管道为`\\.\pipe\verge-mihomo-production-bf6eee0a383fba97d65e64f1811909a6ff1c5e75e246fbdbfcc31fdd3e662bec`；可经此只读HTTP GET访问`/connections`、`/rules`、`/proxies`、`/version`、`/dns/query?name=chatgpt.com&type=A`。
- 实际ChatGPT连接匹配`Match → TIGR → 日本02-TX-IEPL → 日本02TX-LB1..5`，并非DIRECT。TIGR是Selector，日本02是5节点LoadBalance，各节点AnyTLS。
- 部分chatgpt.com连接只有167B上传、0下载；目标IP曾落在157.240、162.125、199.59等异常地址段。当前DNS查询更直接返回`128.242.245.189`（21日只读查询，TTL172）。需与可信DoH返回及TLS结果对照后才能正式判定污染来源。
- `/rules`共538条，没有chatgpt.com/openai.com专用规则；倒数第二条（index536）是`GeoIP cn → DIRECT`，最后`Match → TIGR`。因此匹配GeoIP前可能先走本地DNS，取得错误真实IP后再带往代理，是目前重点假设。
- DNS：enable=true、ipv6=false、fake-ip；nameserver为doh.pub与dns.alidns.com；fallback含doh-pure.onedns.net、ada.openbld.net、223.5.5.5、223.6.6.6；fallback-filter geoip=true，ipcidr仅240.0.0.0/4与0.0.0.0/32。proxy-server-nameserver也是doh.pub/dns.alidns.com。
- 当前订阅UID`RXK9cUgiVlXA`；对应持久规则文件`profiles/rZ6Y7UE8h4zP.yaml`，merge为`profiles/mhmf2BWitcme.yaml`，script为`profiles/sJq1d0KJRjve.js`；全局还有`profiles/Merge.yaml`和`profiles/Script.js`。
- 全局Script包含rules但不包含DNS或proxy-groups修改；只检查过关键字，具体内容及规则合并顺序仍需读代码确认。
- 候选最小修复：将chatgpt.com专用域名代理规则置于GeoIP之前，沿用当前代理组，避免为分流先解析错误真实IP；必要时只为相关域名增加经代理的可信DoH策略。此方案尚未应用/验证，不能写成已修复。
- 任何代理配置修改前备份原文件，限制到相关域名，保留其他规则、节点、订阅和全局选择；验证重载及重启/订阅刷新后仍持久，保留回滚办法。不要把订阅、节点认证或代理secret写入仓库/日志。

#### 已完成的追加代码修改与测试

- `core.py::transient_connection_error`：只读account/read、account/rateLimits/read的明确HTTP408/425/429/500/502/503/504识别为临时故障，继续原有恢复；401/403及永久HTTP错误优先停止，账号/格式与未知发送保护保留。
- 新`test_connection_errors.py`共8项：多种明确HTTP状态格式、只读method识别、永久错误优先、普通数字不误判、重启存档识别、5次后20分钟恢复、401停止、未知发送停止。
- `core.py::failed_connection`：在同一有界重试轮内复用仍健康的只读RPC进程，不再第二次HTTP错误就冷启动；本地TimeoutError、ConnectionError、进程死亡以及重试耗尽仍关闭。
- `test_update.py`对应验证5次错误前保留进程，耗尽后关闭并进入20分钟恢复；`build_release.py`已把新测试加入发布allowlist。Install.ps1本就不安装测试文件，无需为测试修改它。
- 最新全量结果：`python -X utf8 -m unittest discover -q` **164项通过（19.881秒）**；`node --test test_ui.cjs` **40项通过**。测试用正常Windows环境执行，需要localhost socket和原子replace；故意制造的归档blocked/locked/冲突stderr是通过用例，不是未处理失败。
- 上述代码只补恢复逻辑，尚未证明解决当前代理TLS错误；尚未重建发布包或覆盖安装这些追加修改。README/CHANGELOG/VALIDATION的“一次后重建”说明也待同步。

#### 可复用诊断脚本（均在仓库外）

- `E:\work-space\tmp\quota-network-trace.py`：只读官方RPC4轮，筛选reqwest连接/连接池目标日志；不输出账户数据或请求头。已执行完毕，无后台会话遗留。
- `E:\work-space\tmp\quota-proxy-pipe.py`：controller管道GET /connections，只输出OpenAI/ChatGPT/Codex相关连接的路由、目标IP和字节数。
- `E:\work-space\tmp\quota-proxy-inspect.py`：可导入get(path)，带content-length/chunked解析；main输出特定代理组、版本及chatgpt DNS查询。
- `E:\work-space\tmp\quota-proxy-rules-safe.py`：脱敏输出相关规则及当前订阅的merge/script/rules文件引用；不输出订阅URL或密码。
- `E:\work-space\tmp\quota-proxy-dns-config.py`：只读取生成配置中的dns/sniffer段。
- 旧探针`quota-final-probe.py`、`quota-explicit-probe.py`、`quota-live-probe.py`也在同目录。CLI只读诊断需正常用户环境；沙箱localhost会报WinError10013，应使用已授权的提权/沙箱外执行。
- 目前官方CLI选择为npm稳定版0.163.0：`C:\Users\HP\AppData\Roaming\npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe`；桌面另有0.163.0-alpha.10.2，两者均曾出现间歇错误，不应归因成单一版本问题。
- 正式日志位置：安装目录`data/logs/quota-starter-2026-10.jsonl`；逐条含durationSeconds或rpc diagnostics。不要输出账户/认证相关数据。

#### 下一步

1. 对照Clash DNS结果、经代理的可信DoH及真实TLS连接，证明异常IP来源；确认域名规则能避免GeoIP前的错误解析。
2. 备份后最小范围修复持久规则/DNS并重载，检查实际连接命中、IP及握手，不盲目切换代理节点或改变全部网络设置。
3. 用只读CLI连续查询与安装版正常60秒采样验证，记录成功/失败数与时延；若仍失败继续追踪节点，不以一次连接成功宣称修复。
4. 更新README、CHANGELOG、VALIDATION和本文件，必要测试通过后重新打包/覆盖安装，保留最新用户数据、计划、自启及记录；核验安装文件与ZIP一致。
5. 更新D盘的1.7.0更新日志（必要时UI说明），本地commit/Tag完成后供用户审核；不Push、不发布Release。

#### 21日继续调查补充

- 经相同7897代理查询Cloudflare和Google两个DoH均返回chatgpt.com的A记录`172.64.155.209`、`104.18.32.47`，与Clash的`128.242.245.189`明显不同，证实本地解析结果异常。
- 但异常`destinationIP`的部分现有连接仍有大量正常双向数据，不能把它直接当作最终上游目标。核对Mihomo v1.19.32 AnyTLS源码：`metadata.String()`优先Host；仅特定DNSMapping/DNSHosts路径的`Pure()`会清Host。因此“错误IP一定直接导致TLS失败”的判断不成立，根因仍待实测。
- 最前域名规则可避免GeoIP前的本地DNS查询；是否能解决握手超时需对照。当前持久规则文件的prepend/append/delete为空，订阅merge/script未改，全局Script只增加局域网/no-resolve和部分直连域名。
- 新诊断`E:\work-space\tmp\quota-tls-probe.py`将无凭据CONNECT、TLS、HTTP分阶段计时，并按本机源端口关联Clash路由；另由子任务逐个检测当前5个AnyTLS节点，不更改节点选择。所有代理配置此时仍未修改。

## 5小时额度刷新后，预定发送时间未自动调整

- 记录日期：2026-10-21。
- 现象（用户反馈及截图）：5小时额度刷新时间已变为2026-10-21 07:25:47（早上7点半左右），但此前设置的发送请求时间仍停留在当天05:00:00，定时状态没有随额度刷新而自动调整。
- 状态：待解决；按用户要求，等到2026年11月再排查和修复，本次仅记录问题。
- 后续排查：核对额度刷新时间变化后，已保存发送计划的重算与定时状态同步逻辑；具体原因尚未验证。
- 验收要求：额度刷新时间变化后，发送计划和界面定时状态按预期同步调整，不再停留在旧时间。

## 断联追加修复：本次安装结论（2026-10-21 13:41）

- 已按用户选择在master上完成1.7.0追加修复的覆盖安装；main的用户UI备份未修改。全部165项Python和40项Node回归通过。
- 安装前备份：`E:\work-space\tmp\quota-debug-20261021-install-backup`；27条attempts、49条claims的数量及哈希完全一致，SQLite完整性ok，最新schedule/paused/pollWhilePaused/sending保持原值。当前用户暂停发送、开启暂停检测，自启启用，桌面快捷方式指向安装版Start.cmd。
- 13:40:33安装版真实额度读取成功，2.649秒；13:41:34分钟采样成功，0.907秒。25个安装文件与发布ZIP逐字节一致。只读验证未发送模型请求。
- 02:37:18已实际修改代理：当前订阅`profiles/rZ6Y7UE8h4zP.yaml`的prepend加入`DOMAIN-SUFFIX,chatgpt.com,TIGR`；生成配置同步仅增此一条，通过production控制管道的`PUT /configs?force=true`、JSON payload形式重载成功。规则538→539，当前选择不变，新的连接命中DomainSuffix且不再在分流阶段解析目标IP。
- 原字节备份：`C:\Users\HP\AppData\Roaming\io.github.clash-verge-rev.clash-verge-rev\quota-starter-backup-20261021-023718`，含原持久规则与生成配置。回滚需恢复这两个文件，再通过相同官方payload接口重载；不要将包含代理认证的完整配置放入仓库或D盘交付文档。
- 该Clash服务使用私有runtime配置，AppData文件是生成输入；payload重载不改变controller pipe。持久规则已存盘且13:41仍有效，但未通过重启Clash/整机实际验证Verge重新生成；官方prepend合并顺序已核对。应用安装包不会替其他用户修改Clash。
- 对照证据：`E:\work-space\tmp\quota-tls-baseline.json`（原规则8/8无凭据探针成功）；`quota-tls-domain-rule.json`（新规则8/8成功，0.344–4.016秒）；`quota-proxy-node-probe-20261021-023234.jsonl`（5个节点各2次，10/10成功）；两次真实CLI冷启动2.344/1.781秒成功。前后均有短时成功，不能据此证明域名规则就是根治方案。
- **重要反证：** 代理规则生效后到13:40，安装版记录94次成功读取及19条连接失败，最近仍有13:16/13:17额度HTTP失败、13:37路由发现超时。因此“前置域名规则已根治断联”不成立；这些记录发生于追加恢复代码安装前，本次修复的长时稳定性尚未验证。
- 已修的软件问题：有界重试中第二次只读HTTP错误过早冷启动、明确HTTP临时错误可能被永久停止。仅修改本次已验证范围，不改变5次/60秒及20分钟恢复规则，不掩盖错误状态。
- 后续若继续出现失败：优先在实际故障瞬间关联CLI源端口、Clash节点、CONNECT/TLS阶段和代理错误日志；不要再把metadata异常目标IP或一次测速成功当成根因结论。单节点当前检测均正常，尚无证据支持固定某个坏节点。11月待办的计划调整问题按用户要求另行处理。
- 本次交付只保留本地commit/Tag及dist包，D盘同步更新日志，原UI自行调整说明保留。Push（含Tag）与GitHub Release仍未执行，待用户审核。
