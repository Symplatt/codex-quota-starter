# 1.5.17 更新进度

- 已通过本机日志定位：额度恢复后使用量为0%，服务器刷新时间滚动到五小时后，旧逻辑误取消待执行计划，下一轮自动发送。
- 已修复空闲窗口下的计划保留逻辑，并补充跨刷新边界、重启、重连及发送前校验的回归测试；125项Python测试与28项Node界面回归通过。
- 已同步1.5.17版本号、README、CHANGELOG、帮助说明及验证记录。
- 修复已提交：a8fd97f（fix(schedule): preserve manual plans across idle quota resets）。
- 已构建并覆盖安装1.5.17，后台正常连接；安装目录24个发布文件与安装包一致。安装后的实际模块通过6项本次故障回归。
- 已核验原16条触发记录、25条防重复占位及安装前8321条事件记录保留，数据库完整性检查通过；计划、暂停状态、自启偏好及防重复保护均保留。
- 桌面快捷方式目标和工作目录正确，已执行其目标启动脚本并确认运行版本1.5.17。
- 安装包与校验文件已放入D:\根目录，SHA256：c33b3efb879d6dc6571d320c912114a8b8bc1718e34ebe78bef8fe0ce783e171。
- 已检查安装后帮助说明的实际界面；预览待用户确认。调度回归采用模拟额度和时钟，未额外发送真实模型请求。
- 已按用户追加要求完成Push、v1.5.17 Tag与GitHub Release；Tag指向2d44daf，发布说明与CHANGELOG的1.5.17记录一致。
- Windows CI通过：https://github.com/Symplatt/codex-quota-starter/actions/runs/39127155701 。Python测试、Node界面检查及发布包构建均成功。
- Release：https://github.com/Symplatt/codex-quota-starter/releases/tag/v1.5.17 。已上传Windows ZIP与SHA256SUMS.txt，并重新下载确认逐字节等于本地构建产物；D盘副本、已安装24个文件、Tag源码和应用版本均核验一致。
- 远程发布流程已补齐；此前展示的帮助说明实际界面预览仍待用户明确确认。
