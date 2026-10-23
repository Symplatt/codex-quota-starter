# 1.6.0-pre.5 安装路径、横幅与日志布局（2026-10-24，审核通过）

- 用户已明确审核通过；CHANGELOG、README、帮助及版本已同步。新增实际安装路径，错误横幅只将统计卡整体下移，日志卡片随内容收紧并保留一屏上限及内部滚动。
- 验证：隔离发布目录223项Python、61项Node通过；真实浏览器横幅3种窗口及日志4种窗口24组检查通过。正式ZIP共49文件逐项核对，包内5项连接基线测试通过，SHA-256为3cb9e468cf48ab50fe4594f1545f991236b7a9d325ddcd07d8f022f5c473822e。
- 本机已在源码目录E:\work-space\quota-starter启用pre.5，桌面快捷方式实际启动成功；真实后台linked=true、idle，自启动已启用且无错误，软件信息路径正确、日志无额外留白。28个运行文件与ZIP一致，SQLite完整性ok，1条attempts、2条claims、0个预设任务以及计划/暂停/暂停检测/任务设置内容指纹全部保留。
- 本机原安装目录已按用户要求移除，原数据备份在%LOCALAPPDATA%\QuotaStarterBackups\installed-before-source-switch-20261024-022213；本次重启前数据库备份在dist/release-pre5/runtime-backup.sqlite3。桌面、开始菜单、自启动均指向源码目录。其他用户安装仍默认使用%LOCALAPPDATA%\Programs\QuotaStarter，通常位于C盘用户目录。
- 已准备D盘独立未选复选框更新日志；ZIP保留于dist，按约定不放D盘。远程commit/Push/Tag/Release及公开资产最终核验正在执行；核验记录见差异文档/1.6.0-pre.5/发布核验.json。

# 1.6.0-pre.4 预设任务（2026-10-23）

- 已完成：预设任务全部功能及最新修改：本机模型检测/标签复选、AppData默认目录、表单顺序/加粗/26px行距、状态独立列/右对齐、详情视觉左移6px、详情目录内路径缩写/标题右侧等高居中状态胶囊/去重复进度、标题下更新说明/28px留白、导航首项回页顶/末项自然底部、时间轴对齐、检测中时间操作队列；重新检测/重新连接/恢复运行更新周恢复时间，每次五小时100%后补读一次周额度，保留5055980的UI调整。
- 验证：223项Python、59项Node通过；真实GPT-6.1 Sol/low任务在独立Windows沙箱执行75秒等待并写入文件及结果MD，期间两次新额度检测；真实表单/MD导入/默认目录/模型复选/回满等待/取消/定位，以及详情胶囊与路径显示检查通过。
- 已安装页面：http://127.0.0.1:8769；pre.4正式ZIP覆盖安装及真实启动通过，原手动计划、暂停/暂停检测/自启设置、32条attempts及58条claims逐项保留，28个程序文件与ZIP一致，SQLite完整性ok；桌面快捷方式实际启动新版通过。隔离审核后台18779已停止。
- 审核材料：差异文档/1.6.0-pre.4，含完整代码、SHA-256清单、验证、真实截图及D盘复选框日志草案。
- D盘独立复选框日志已更新并逐字节核验：D:\Quota-Starter-1.6.0-pre.4-更新日志.md；22个选框全部未选，规则已加入AGENTS.md；ZIP不放D盘。
- 已完成：2026-10-23用户审核通过；发布提交09f0af19ace7486ed9d966b4b1a2595d30a020d8及v1.6.0-pre.4 Tag已推送，预发布公开：https://github.com/Symplatt/codex-quota-starter/releases/tag/v1.6.0-pre.4。分支/Tag Windows CI均成功（39967817730、39968260644）。首次CI的Windows临时短路径断言已在测试中规范化，程序行为不变。
- 公开匿名下载回验：两份资产与本地一致，49个文件对应发布Tag、28个安装文件对应公开ZIP；SHA-256为518e901643409656fa2a614567d77baf40e8fafcd77bf4480d5705bcdaaaf33a。Latest仍为v1.5.23，main、archive及旧v1.6.0 Tag未变。安装核验后发生的用户新手动计划已保留。
- 最终记录为单独docs提交；README项目信息残留的pre.3当前版本文字及pre.1历史表述已纠正，已公开Tag和资产保持原样。备份为dist/release-pre4/installed-backup。
- 教学文档：D:\Quota Starter 教学文档.md，含只读界面预览、pre.3分线转正式与旧v1.6.0标签冲突、网页/CLI发布、安装数据核验和故障恢复；预览脚本与文档内代码语法已验证。没有执行正式版转换。

---

# 待解决问题

## 1.6.0-pre.3 连接修复、安装与预发布完成（2026-10-23）

- 用户要求先恢复本机pre.2、修好连接并发布pre.3，随后明确允许修改连接功能和重连机制。本次连接修复不再受1.5.23连接源码冻结约束；保留账户校验、防重复与未知发送保护。
- 已完成：从GitHub下载pre.2并核验SHA-256；备份pre.1安装/SQLite后恢复pre.2，25文件一致，31条attempts、56条claims及计划/暂停/暂停检测保留，桌面快捷方式启动通过。
- 已定位：find_codex优先命中桌面PATH中的0.163.0-alpha.11.2；npm稳定版0.163.0已安装。原版每次只读HTTP失败均重建进程，耗尽一分钟后等待20分钟，恢复时单次失败又等待20分钟。
- 已完成修复：禁用重复系统/PAC发现与引导回退、优先npm CLI，已返回的临时HTTP错误复用健康进程，断管/超时重建；整轮失败30秒至5分钟退避；认证/未知发送保护保留。178项Python、45项前端通过；600.5秒连续检测取得10次新额度，主动终止测试子进程后自动重建成功，模型发送0次；无代理变量登录环境实际读取成功。
- 版本、CHANGELOG、README、帮助及验证材料已同步；D盘复选框日志已保存并核验。42文件正式ZIP已构建并逐项对齐源码，ZIP内19项连接/代理及指纹测试通过；从最终ZIP覆盖安装pre.3，25个安装文件一致、数据库完整性ok，31条attempts及56条claims、最新计划/暂停/暂停检测/自启保留，桌面快捷方式验证启动。安装后已真实linked=true、idle，正常60秒节奏连续3次新读数成功，最终无连接错误。
- 已完成：发布代码095a2596a6470ba6109f19f1a7efd5dd8a1f358f及v1.6.0-pre.3 Tag推送，公开预发布https://github.com/Symplatt/codex-quota-starter/releases/tag/v1.6.0-pre.3；分支和Tag的Windows CI成功（39905131337、39905131333）。
- 公开资产回验：ZIP/SHA256SUMS与本地一致，42个源码文件对应Tag，25个安装文件对应公开ZIP；SHA-256为45130e006c67eddbc5cef8c42a8c1273918302f835fbd8f3efabc805aa900bb1。Latest仍为v1.5.23，main、archive和旧v1.6.0 Tag未变。
- D盘复选框日志已按最终结果更新；核验明细见差异文档/1.6.0-pre.3/发布核验.json。最终安装备份为%TEMP%/quota-pre3-final-installed-backup-20261023。安装后观察到新的计划编辑，保留用户最新计划，不从备份覆盖。
- 本次无待发布步骤。稳定性证据为10分钟真实连续检测、一次真实子进程故障恢复及安装后的正常节奏读取；不宣称网络/上游永久不会故障。


## 1.6.0-pre.2 更新安装与预发布完成（2026-10-23）

- 已完成：额度统计纵轴固定0%至100%，绘图区不足200px时每20%一格，达到200px时每10%一格；三个时间轴标记外径均为14px，两个刷新节点保留原灰绿色空心样式；发送节点保留墨绿色实心与拖动。周额度更新时间保持单行，不少于24小时仅显示日期，不足24小时仅显示时分秒，悬停保留完整预计更新时间。
- 已验证：44项前端测试、5项连接基线测试通过；绘图区199/200/201px及两种额度/三个范围/空与非空数据144组密度校验；真实后台只读页面的0%至100%、每20%刻度、三个标记实际外径一致、两端空心样式正确；周更新日期为18px单行。24小时边界、跨午夜和缺失时间已测试。预览http://127.0.0.1:18762/仅代理安装版实时数据，拒绝POST，标注开发版本，不代表已安装。
- 版本为1.6.0-pre.2；README与帮助同步；完整HTML、帮助、差异、校验和复选框日志在差异文档/1.6.0-pre.2。
- 审核图片：C:\Users\HP\.codex\visualizations\2026\10\23\01a166d1-58d9-7063-9d73-cc9c17cd2d17\quota-pre2-adaptive-ticks.png。
- D盘复选框独立日志已保存并逐字节核验：D:\quota-starter-1.6.0-pre.2-更新日志-2026-10-23.md。CHANGELOG已在审核通过后同步。
- 用户已用✅确认上一轮空心圆点与周时间修改；随后补充短图20%刻度要求。用户随后明确要求“先完成本次更新再说”，已授权按本轮实际内容完成安装与预发布；连接异常另记，不扩展本次连接修改。
- 已完成：批准后同步CHANGELOG，163项Python与44项前端通过，40文件ZIP逐项对应源码且ZIP内5项连接指纹通过；从ZIP覆盖安装1.6.0-pre.2，25个安装文件一致，数据库完整性ok，31条attempts、56条claims与计划/暂停/暂停检测完全保留，自启仍启用，桌面快捷方式已验证启动。备份位于%TEMP%/quota-pre2-installed-backup-20261023。
- 正式安装界面已核验：版本v1.6.0-pre.2、两个14px空心刷新节点、周日期单行及短图20%刻度。既有account/read超时仍在原等待重连状态，本次没有修改连接代码。
- 已完成：发布提交ca8453db0a97d57452f01f29a335761c8f4dd256及v1.6.0-pre.2 Tag已推送，预发布Release公开；说明与CHANGELOG一致。分支和Tag的Windows CI均成功（39900490837、39900490822）。
- 公开下载回验：ZIP及SHA256SUMS与本地一致，40个源码文件与Tag逐字节一致，25个安装文件与公开ZIP一致；SHA-256为50149925ee2c8dd85d55646845023eac5b0d8c3da1789fb5ac969a6dd0316380。
- 发布地址：https://github.com/Symplatt/codex-quota-starter/releases/tag/v1.6.0-pre.2。Latest正式版仍为v1.5.23；main、archive分支和旧v1.6.0 Tag未变。本完成记录单独提交，不移动发布Tag或重打发布包。
- 本次无待发布步骤；既有账户连接异常按用户要求留待后续，不将本次发布描述为连接已恢复。详细证据见差异文档/1.6.0-pre.2/发布核验.json。


## 1.6.0-pre.1 预发布与分支拆分完成（2026-10-23）

- 已确认撤下范围：v1.5.24、v1.6.2、v1.7.0 Release；保留旧Tag与提交历史。旧Release说明及附件已备份到项目外tmp/quota-release-withdrawal-20261023。
- release/1.6.0直接从v1.5.23分出；archive/1.7.0-plus保留远程main的46b922a代码，main不重写。采用新Tag v1.6.0-pre.1，保留原v1.6.0 Tag。正式Latest仍为v1.5.23。
- 已同步版本、README及CHANGELOG；旧CHANGELOG条目归档到差异文档/1.6.0/撤下版本历史.md。
- 已完成：40文件白名单ZIP构建/校验；新分支163项Python及42项前端回归、ZIP内5项连接指纹校验；备份并覆盖安装，真实读取成功、25个安装文件一致、数据库完整性ok，30条attempts和54条claims及设置保留，桌面快捷方式验证启动。
- 已完成：发布代码0434fb1b28d6c487cbe0d3c44c8a48693d3e8abd、release/1.6.0分支和v1.6.0-pre.1 Tag已推送；该发布代码直接以77e35bd7f71f260b468fb0e8bdfae7a699af93a2为父提交。archive/1.7.0-plus已推送并与原main的46b922abfcb11c744b6ee1501e8d3bf4df913a9c一致；main和旧Tag未变。
- 预发布：https://github.com/Symplatt/codex-quota-starter/releases/tag/v1.6.0-pre.1，prerelease=true、draft=false。v1.5.24/v1.6.2/v1.7.0 Release及附件已撤下，其他Release保留，Latest仍为v1.5.23且原资产未变。
- 预发布分支及Tag的Windows CI均成功（39850489985、39850488987）；公开ZIP重新下载后与本地及Tag的40个源码文件逐字节一致。ZIP SHA256为20e4957c3cfa1a0bca1895b2eb3c175383afc8fabcaf112da88b55d6a4d64022。
- 安装后再次真实读取成功。后续检测到用户通过页面新建/修改手动计划，相应schedule操作事件晚于升级核验，保留该新计划，不用安装前空计划覆盖。请求/防重复记录未变。
- D盘复选框日志已按实际结果更新；远程、分支、Tag及下载核验结果见差异文档/1.6.0/预发布核验.json。本完成记录为文档提交，不移动发布Tag或重打发布包。
- 剩余验证限制：真实五小时/周自然恢复边界长时观察尚未完成。

## 1.6.0 开发与审核准备完成（2026-10-23，替代上一轮1.7.1审核方案）

- 用户要求以v1.5.23为连接基线，移植差异文档的功能与UI，所有CLI、代理、接口、超时、重试和本地连接端口保留1.5.23；截图明确忽略。
- 用户已确认保留前面新增的周额度预计时间、过期计划恢复自动、按钮文字样式一致和仅统计卡动态调整的100vh布局。
- 已备份上轮全部29个待改文件到E:\work-space\tmp\quota-before-160-20261023，含SHA-256清单；原1.7.1方案不丢弃。
- 已恢复12个连接/启动完整文件及core的14个连接/阶段方法到v1.5.23原始源码；默认网页8769。连接基线5项校验通过，保护6个常量及tick重试分支。远程连接增强测试已归档，不把那些新行为当成本次预期。
- 功能/UI已完成：拖动计划、分钟采样、统计记忆、暂停期间检测开关、月度日志/迁移、日志请求证据、错误横幅及图标，以及用户确认的四项补充。163项Python和42项前端测试全部通过；原有1.5.23完成间隔保持不变，未为旧测试调整生产连接方法。
- 独立只读真实预览http://127.0.0.1:18761已连接，attempts为0。实际验证弹窗保存、键盘十分钟调整、刷新后统计记忆及按钮10项样式一致；目标浏览器窗口maximized，CSS可视区1691×818，统计底部798+20=818，日志从818开始。标题和两张上方卡尺寸固定，只有统计卡动态调整。
- 全部当前实施材料在“差异文档/1.6.0”：实施说明、1.6.0完整HTML和帮助、UI净差异、软件与测试净差异、连接基线指纹、当前源码校验、审核日志。项目外截图：E:\work-space\quota-ui-review\1.6.0-baseline-1523\实际界面.png。
- D盘复选框独立日志已保存并逐字节校验：D:\quota-starter-1.6.0-更新日志-2026-10-23.md，SHA256 DDC82351254071C85B8C7434C9F97E2EF9CB6997BE8BCFF26475CE55467375F1。未交付ZIP。
- 已查明远程有同名v1.6.0 Tag，当前无对应Release；本次未动Tag。正式发布需在审核后确认同名Tag处理，不能静默覆盖。
- 按最新流程：代码与测试完成后保存D盘复选框独立日志，等待审核；审核通过后才同步项目CHANGELOG和正式构建/覆盖安装/发布。当前CHANGELOG保留Git现有历史并撤回上轮未审核1.7.1草稿，未提前替换旧1.6.0记录；旧dist仅为以前构建，不代表本次正式包。
- 当前等待用户审核；审核通过后还需明确同名远程Tag处理，再按顺序同步CHANGELOG、正式构建、覆盖安装及数据核验、commit/Push/Tag/Release和最终校验。未进行自然五小时/周恢复长时观察，不把模拟测试称为自然边界实测。

以下为上一轮历史记录，不能作为本次1.6.0连接基线或交付状态。

## 1.7.1 开发、比对与验证完成，等待审核（2026-10-23）

- 用户确认：以v1.5.23比较GitHub远程main；功能、UI、Codex CLI连接使用并列标题；功能允许伪代码，UI必须完整原始HTML；本次取消重连机制及指数退避更新，禁止影响原连接机制。
- 已完成：手动重连、自动恢复和只读检测失败后恢复时，用最新快照检查过期计划并恢复自动；普通空闲检测保留原计划规则；恢复自动提示按计划去重。重连核验这轮不发送请求。
- 已完成：周刷新时间按账户持久保存；首次五小时剩余100%时记录，此后仅在不足100%恢复100%时更新；重复满额、普通检测、重启不滚动更新；不增加额度查询或模型请求。
- 已完成：两个可用按钮的文字颜色、字体、字号、字重、行高、字距、样式、装饰和透明度一致。标题、上方两卡及留白不动态缩放，仅统计卡吸收剩余可视高度。
- 比对报告：差异文档/v1.5.23-remote-comparison.md，附完整HTML/帮助/图标、UI全量diff、校验值、25文件清单、Python/JS语法树与DOM/有序CSS复核。固定比较77e35bd7f71f260b468fb0e8bdfae7a699af93a2至46b922abfcb11c744b6ee1501e8d3bf4df913a9c；远程领先14提交，结束前通过GitHub connector再次确认main未变。报告不混入本次1.7.1修改。
- 已纠正文档中的净差异：1.5.23本来就有统一事件归档、操作日志中的完成证据及周期数值判定；中间版本的独立触发文件不是基准行为。旧文件备份顺序、过期文件分支和远程首屏适配条件按实际代码列明。
- 已验证：175项Python、42项Node回归全部通过；Python编译和git diff --check通过。修复旧HTTP测试只接受单引号、无空格token声明的问题，应用来源/token校验不变。
- 真实预览：独立后台http://127.0.0.1:18771，暂停发送且自动检测关闭，真实只读查询成功；安装目录、安装版进程与数据未修改。预览库attempts为0。用户在预览中设置的计划保留。
- 默认浏览器窗口已确认maximized；真实内容区1691×818：标题40.67px、上方两卡各335.33px，统计卡346px，顶部452px，底部798px加20px=818px；日志从818px开始。1280×900、1280×600、1024×768、1000×1080也通过，只有统计卡随高度变化；分数像素误差小于0.02px，无横向溢出。
- 审核图片在E:\work-space\quota-ui-review\1.7.1：maximized-desktop.png为真实后台预览；weekly-date-layout-sample.png仅为日期排版场景，不代表真实额度已恢复100%，未保存样例时间或改变检测条件。
- 连接不变已核对：rpc.py、supervisor.py、windows_settings.py、启动/停止及诊断脚本未改；core连接/失败/恢复/发送方法与五个间隔常量均保持原AST。未调整CLI选择、代理、协议、进程关闭/复用、重试次数或时间预算。
- 版本、README、帮助、CHANGELOG、VALIDATION已同步到1.7.1；ZIP及SHA256SUMS仅保留项目dist。源比较报告独立保留源码“差异文档”，不随安装器复制。
- 未验证：真实五小时/周自然恢复边界的长时观察、覆盖安装、安装后数据保留；前两类逻辑已使用受控快照和回归覆盖，不宣称已完成真实自然边界实验。
- 待办：用户明确审核通过后，执行项目安装与数据保留核验、D盘更新文档交付，再commit、Push、Tag、Release及远程资产一致性核验。尚未执行上述正式交付/发布步骤，ZIP不放D盘。
- 文内1.7.0条目为历史记录，其审核例外不适用于1.7.1；此前“11月再处理计划问题”的延期已被本次明确更新指令取代。

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
