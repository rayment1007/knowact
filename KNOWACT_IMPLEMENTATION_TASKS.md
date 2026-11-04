# KnowAct report implementation tasks

更新日期：2026-11-04（Asia/Kuala_Lumpur；初次审计 2026-11-01）
代码基线：`ad6ca1b` — feat(memory): complete phase 1 and phase 2 foundations（初次审计基线 `2bd0478`）
当前状态：**Phase 4 的 M1 会议日期冲突审核链路已实现并通过本地回归：明确身份 → 五类规则比较 → 独立人工审核 → Current / History。完整 Phase 4 仍有语义匹配、其他事实类型和复杂时间规则待扩展；Copilot 整合待 Phase 5，完整 D3 待 Phase 6。新入口仍为默认关闭的开发功能，浏览器端到端、生产迁移和部署尚未验证/执行。**

本文件是后续开发的主任务清单。目标是逐步实现用户本次提供的 `KnowAct_FYP_Report_Formatted_v4.docx` 中的产品行为，优先完成 Conflict-Aware Project Memory，并保留现有 Sources → Knowledge / Actions 工作流程。完整需求及代码差距见 [Report v4 gap audit](docs/plans/REPORT_V4_GAP_AUDIT.md)。

## 已确定的范围和决定

- **用户本次确认**：没有固定截止日，按阶段推进和验收。
- **用户本次确认**：第一版只处理用户选定的 source。点击 **Add to Knowledge** 后提取、比较并生成待审核内容；Gmail / Calendar Sync 不自动启动批量事实提取。
- 产品对象是个人 IT Project Coordinator；保留 Dashboard / Workspace → Sources、Knowledge、Actions，以及 Search、Copilot、Settings。
- 支持 Gmail、Google Calendar、英文 text-based PDF / DOCX / TXT、manual notes。暂不增加连接器、团队审批、模型训练或知识图谱平台。
- report v4 是需求范围依据；GPT 建议和 TXT 是待审查的设计输入，不能当成已实现或已验证的事实。
- 用户已要求继续 Phase 4；本次完成 M1 的身份确认、比较与审核链路，不自动进入 Phase 5 或部署。
- **用户已确认 D3**：普通 Remove 删除本地原始 source，清楚告知保留已批准事实所需的最少 evidence 片段；Purge 清除相关内容副本、evidence text、embedding 和 AI logs，受影响历史显示 evidence removed。两者都不删除 Google 端资料。具体实现属于 Phase 2/6。
- 一个阶段可以分成多个小批次。每个批次必须交代实际改动、验证结果、遗留问题和下一步，并更新本文件。

## 对建议方案的判断

采用 **结构化事实 + 确定性规则 + 时间及来源上下文 + 受约束的 LLM 辅助 + 人工审核** 是合理方向，也与 report §2.5、§3.5.5、Tables 3.6 / 3.8 / 3.9 一致。但需要补上以下条件：

1. **Project grouping 是身份匹配前提。** 当前 org-scoped BusinessEntity 不能直接代表个人项目。先确定 owner 和 project；Unassigned 不能参加所有项目的比较。
2. **fact_key 不能直接相信 AI 生成的字符串。** 需要稳定身份、受审核的别名及事件区分。重复会议还要区分 occurrence；不能把会变化的会议日期直接当作身份主键。
3. **不同值不一定 conflict。** 时间区间不同、提议/问题、引用旧邮件、多人负责或不同会议可能都不冲突。日期缺少年份/时区/精度也不能随意补全。
4. **“rescheduled” 不足以单独证明 Revision。** 必须确定同一事件、替换对象和时间上下文；延迟导入的旧通知不能覆盖后来已批准的版本。
5. **SourceProposal.version 不是事实版本历史。** 它目前只防止审核旧草稿。事实版本必须另有不可变的已接受内容、前驱与 current 状态。
6. **Same 不等于自动确认新来源。** 可去重连接支持证据，但不能修改值、制造新版本，或把未经检查的来源包装成已审核权威。
7. **LLM/API 失败不是 Unclear。** Unclear 是内容本身无法确定；超时、解析失败和检索失败要有独立错误/重试状态。匹配服务失败不能伪装为 New。
8. **保留 summary，并增加原子事实层。** 普通背景摘要不是单一事实；不应强迫每个旧 KnowledgeItem 变成一个日期或状态事实。一个 source 可以提取 0..N 个事实，逐项呈现和审核。
9. **评估要提前设计。** 在调 prompt 前确定预期答案和保留测试集；测 identity、evidence 和 review control，不能只看五类标签 accuracy。

FactTrack 可作为原子事实和时间有效性设计的参考，其研究任务是 story outlines，不是本系统的业务验证。原论文：[ACL Anthology, NAACL 2025](https://aclanthology.org/2025.naacl-long.144/)。本项目不宣称复制其结果，也不需要训练独立 NLI 模型。

## 全程必须保持的规则

- 个人资料、事实、项目、提议、证据、历史和派生 AI context 都同时检查 organization 与 workspace owner。UI 隐藏不是访问控制。
- 原始 source 在成功捕获和权限检查后可搜索；不要求逐条批准。AI answer / summary 显示不会自动成为 confirmed memory。
- `relationship`、`review_state`、`version/current_state` 分开。Superseded 的旧值仍可保持 historically approved，不能改成 Rejected。
- 对单值事实，同一 owner + project + fact identity 最多一个 accepted current value。多人等集合值要另定值契约，不能随意产生多个 current heads。
- 已批准的事实内容不可原地覆盖；改动通过新 proposal / successor。Review 和证据来源能追溯到当时的快照。
- Approve 必须检查提议版本、预期 current head、来源版本/指纹及证据。冲突审核期间 current 改变时，返回可解释的 stale 状态并重新比较。
- 同一事务提交新版本、旧版本 supersede、证据、review、audit；唯一约束和并发控制作为后盾。重试不产生重复版本。
- Edit 保存为 Unconfirmed；Reject 不改已确认值；Keep unresolved 保留当前值并显示警示。Conflict / Unclear 必须先解决身份或明确选定审核后的值，不能靠一个含糊的 Approve 自动猜测。
- 保存 summary 不等于一次批准所有原子事实。批量批准如以后提供，也必须明确展示选中的事实、值及证据。
- 每个事实都有逐事实 evidence passage / locator。用户编辑事实值后，重查证据；如是用户自己的声明，明确记录为用户来源/说明，不能继续伪称原文支持另一值。
- 所有生成/确认入口共用事实服务；不能从旧 `/knowledge/.../confirm` 或其他 API 绕过规则。
- Fact approval、Action acceptance、Gmail Send、Calendar create/update/delete 是独立决定。任何一个都不能顺带授权另一个。
- 已有 local removal → 后续 Sync 可重新导入 Gmail / Calendar 的行为保留。Google 端 delete 是另一种需要明确批准的操作。

## 阶段总览

| 阶段 | 交付目标 | 主要 report 对应 | 状态 |
|---|---|---|---|
| Phase 0 | 本次需求/代码审计和任务计划 | 25 FR、8 NFR、3 objectives | 完成；用户已批准进入 Phase 1 |
| Phase 1 | 固定事实契约，补个人隔离和审核基线 | FR-13/14/20/22/24，NFR-02/04 | 本地实现与验证完成 |
| Phase 2 | 事实身份、不可变版本、证据、review 和事务 | FR-09/13/14/15/23 | M1 基础本地验收完成；P2-08 全量 D3 清除与 Phase 6 联合完成 |
| Phase 3 | 选定 source 的结构化提取与项目归属 | FR-04/05/10/25 | M1 meeting-date 提取与候选编辑本地完成；其他 fact types 尚待扩展 |
| Phase 4 | 五类比较、冲突审核、版本历史 UI | FR-11–15/23 | M1 date-only 审核链路本地完成；语义匹配、其他类型和复杂时间待扩展，浏览器演示未验证 |
| Phase 5 | Copilot / Search / Brief 使用当前事实 | FR-06–09/16/23 | 待开始 |
| Phase 6 | 补齐其他 report 功能和外部操作追踪 | FR-01–05/16–22/23/25 | 待开始 |
| Phase 7 | 完整评估、失败恢复及性能记录 | §3.6，全部 NFR | 待开始 |
| Phase 8 | 部署验证、文档和 diagrams 同步 | 全部 FR / NFR 发布验收 | 待开始 |

Phase 1 中的安全/准确发送基线是新功能前置条件。Phase 2–4 先交付下面的窄范围 M1，再扩展其余类型、来源和关系，不能要求所有 Phase 2–4 的扩展任务完成后才演示。Phase 5 完成后才能声称 Copilot 已与 Conflict-Aware Memory 整合。每阶段都做相关测试，Phase 7 是整体验证，并非到最后才测试。

### M1 Minimum Viable Conflict Feature

一个 owner、一个显式选择的 Project、一封新 Email、一种会议日期事实。先由可追溯的人工批准建立 UAT Date = 17 Sep，再对用户选定、明确写明 rescheduled to 18 Sep 的 Email 执行 Add to Knowledge → 原子事实提取 → 同一 fact identity 匹配 → Revision → current/proposed/evidence 对照 → 用户批准 → V1 Superseded / V2 Current。

- M1 只取 Phase 2 的最小版本/证据/review 事务，Phase 3 的 Email + meeting date 提取，以及 Phase 4 的 exact identity + Revision review；不等待全部五种 fact types 或 document pipeline。
- New 用于建立初始事实；重复请求/相同证据不得造第二个版本。缺年份、歧义身份、不同项目、不同用户、旧 source/head、非明确替换等不支持案例安全保留待澄清或拒绝，不能用猜测补齐演示。
- M1 必须验证 Edit/Reject 保持 current、双击批准幂等、来源可追踪、个人隔离；不因缩小范围略过这些边界。
- M1 演示后再推进五种 facts、四类 sources、完整五类关系、语义候选和更复杂时间情况；M1 完成不代表整个 Conflict Feature 已完成。
- General Summary + 0..N Structured Facts 分开呈现和保存；批准 summary 不能批准隐藏的 facts。

## Phase 0 本次审计和计划

- [x] P0-01 读取指定 report v4 的正文、25 项 FR、8 项 NFR、方法、review outcomes、数据设计及评估计划。
- [x] P0-02 读取 GPT 补充 TXT，逐项检查它与 report 和当前代码的差异。
- [x] P0-03 审查 Knowledge、SourceProposal、提取、review、个人隔离、Search / Copilot / Brief、外部操作及测试路径。
- [x] P0-04 执行一组现有离线回归：70 passed。记录范围限制，未把它当作线上或 conflict 验证。
- [x] P0-05 建立本清单和 FR / NFR gap matrix。
- [x] P0-06 用户审阅任务计划并要求开始 Phase 1；已加入 M1、可选 LLM-only baseline 和已确认 D3。

Phase 0 审计没有执行 migration、访问线上数据库、调用 live OpenAI、发送 Gmail、改变 Google Calendar、修改 report 或部署。Phase 1 的独立测试库验证另见下方 checkpoint。

## Phase 1 固定契约与保护现有数据

**目的：** 在增加 memory 前，把 owner/project 和现有确认入口的规则统一。先保留当前可用工作流程。

- [x] P1-01 将 report §3.5.5、Tables 3.6 / 3.8 / 3.9 转为领域契约和状态转换表；列出全部知识写入/读取入口。
- [x] P1-02 固定五类事实的值结构：deadline、meeting date/time、project status、decision、responsible person。枚举名称在设计中确定，不能因 TXT 的示例名称遗漏 meeting time。
- [x] P1-03 明确 owner + project + fact_type + canonical fact_key；定义重复事件、别名、单值/集合值、date vs datetime、timezone/precision、source time vs effective time 和 normalization version。
- [x] P1-04 为 Core Source / Knowledge / Action / Decision / Brief / entity、Dashboard summary 及其消费者补个人隔离策略；权限同时覆盖 list、detail、search、mutate、AI context、history。CWI 的 user-scoped 来源不代表旧 Core endpoints 同样安全。不可恢复 owner 的旧记录先隔离，不能猜给当前用户。
- [x] P1-05 设计 owner-scoped project 的最小实现，优先复用 BusinessEntity；project 的创建/选择/重命名/删除不能使别人的事实进入比较或丢失历史。
- [x] P1-06 统一旧确认/拒绝的状态规则，防止 Rejected 被普通 confirm 复活，或 Reject 直接否定既有 confirmed current。为后续事实服务确定接入/禁用策略；同时清点并硬化或停用旧 AI Brief / Decision 等非当前 UI 但仍注册的入口，不能只修 UI 正在调用的路径。
- [x] P1-07 复核“已创建 Gmail draft → KnowAct 再编辑 → Send”的实际请求路径，并用 fake provider 测试确切发送内容；如确认为本次静态审计发现的旧 provider-draft 风险，先修复再开放后续发送测试。
- [x] P1-08 写最小 golden scenarios 与预期状态，包含跨用户/项目、过期审核和错误证据；先固定关系定义，再调实现。
- [x] P1-09 记录 migration / legacy 数据处理方案以及批准前后数据计数。个人隔离如需新增 owner 字段，可在本阶段做必要的 additive migration 和测试回填；事实版本 schema 留在 Phase 2，任何生产迁移单独在发布阶段执行。

**验收：** 同 organization 的第二个用户不能读取、修改或把他人的资料送入自己的 AI context；现有 Add to Knowledge / Actions 可用；明确 Send 的内容与所审核内容一致；领域契约和测试预期可读可复现。

**阶段演示：** 两个测试账号各自查看同名项目，数据相互隔离。个人隔离与审核基线通过后，再进入 Phase 2 的事实版本数据设计。

## Phase 2 事实数据和版本审核基础

**目的：** 先用可控候选验证“旧值保留、新值需批准”，不依赖 LLM 是否判断正确。

- [x] P2-01 采用 additive migration；实现 FactIdentity、FactVersion / Proposal、EvidenceLink、ReviewRecord 的职责。是否分为四/五张表由 Phase 1 设计确定。
- [x] P2-02 保留 KnowledgeItem 的一般 context / legacy 用途；建立与 structured facts 的清晰关联，避免两套 independently confirmed current values。
- [x] P2-03 实现 owner/project 外键与一致性约束、current head 唯一约束、不可变 approved version、predecessor / supersedes 链及所需索引。
- [x] P2-04 Evidence 保存 typed origin、source identity、原文片段、page/chunk/span 或可定位位置、source revision/fingerprint、source time 和已知 effective time。除 local UUID 外记录稳定 provenance：Gmail connection + message ID、Calendar connection + calendar ID + event ID、document asset + checksum、note ID + content revision，以应对重导入的新 local ID。
- [x] P2-05 实现统一 review service：Save Edit、Approve New、Approve supported replacement、Reject、Keep unresolved；保存实际审核的 payload 和 append-only decision event。
- [x] P2-06 批准事务验证 expected proposal revision + expected current head + source revision；使用锁/compare-and-swap、幂等键和数据库约束处理双击及多标签页并发。
- [x] P2-07 Same evidence 连接可幂等去重；不更改值/现有 review state，不增加 successor。新证据自己的检查状态可解释。
- [ ] P2-08 制定 source 移除、privacy purge、reimport 和 evidence snapshot 的生命周期；按“已确认决定 D3”落实，不暗中保留用户要求清除的文本。
- [x] P2-09 为旧记录生成 owner/provenance inventory。只有可证明的 owner 自动回填；结构化事实重新提取后仍需 review，不能批量伪造审批。
- [x] P2-10 在真实测试 PostgreSQL 上验证 migration、唯一约束、事务回滚与两个并发审批；SQLite 结果不代表 Neon/pgvector 行为。

Phase 2–3 的新入口先限于测试/开发开关；Phase 4 的统一比较和 review 路径完整前，不把未完成的 structured-fact approval 作为生产可用流程开放。

**2026-11-03 验收说明：** 上面已勾选项按 M1 的 complete-date MEETING_TIME 范围实现，不表示五种事实类型的提取/比较已经完成。P2-02 保持 general context 与 facts 独立，使用同一 owner/project/source provenance；summary + 0..N facts 的提取批次和界面由 Phase 3 接入。P2-04 不猜有效期、年份或时区；未知 source-authored time 保持空。P2-09 已有只读 inventory 工具和合成测试，未扫描生产数据库。

P2-08 **部分完成**：已审计内容存储，建立稳定 origin、原始来源 unavailable 标记、重导入去重和内部 fact-layer redaction primitive。普通移除后的未批准快照最小化、旧 summary/payload/log 副本、文件与 DB 协调清除、缓存失效、Purge UI 和完整重试/恢复仍未完成，继续关联 Phase 6；不把该项勾成完整 D3 已实现。实现、约束和迁移边界见 [Phase 2 runbook](docs/plans/PHASE2_FACT_MEMORY_RUNBOOK.md)、[content lifecycle](docs/plans/PHASE2_CONTENT_LIFECYCLE.md)、[development API](docs/plans/PHASE2_DEVELOPMENT_API.md)。

**验收：** 在没有 AI 的情况下，也能完成 V1 current → pending V2 → approved V2 / superseded V1；Edit / Reject / unresolved 不改变 V1；并发或重试最多产生一个 accepted current；任一步失败全部回滚。

## Phase 3 用户选定 source 的事实提取

**目的：** 扩展现有 Add to Knowledge，让用户看到摘要及逐项可编辑、有证据的事实候选。

- [x] P3-01 使用现有 `/workspace/source/{kind}/{id}/proposals` 入口；扩展 provider schema 返回 summary + 0..N atomic fact candidates，不把 narrative summary 强当成一个 fact。
- [x] P3-02 选定/确认当前用户的项目；AI 可建议项目归属，但不能偷偷创建项目或把 Unassigned 当作所有项目。
- [x] P3-03 四类 source 统一输入契约：Gmail 原文、Calendar event identity/date/time、document text/chunk/page、manual note revision。保留每一来源自身的时间及 locator。
- [x] P3-04 输出 fact type/key/value、statement modality（assertion/proposal/question/uncertain）、previous value / change cue（若有）、evidence 与时间精度；标注模型声称与用户 confirmed 的区别。
- [x] P3-05 规则验证 JSON 类型、原文 quote、关键值及定位；拒绝编造证据、日期补全和不支持的类型。0 facts 是正常结果；AI failure 是可重试错误。
- [ ] P3-06 区分 repeated event、多人负责、状态范围、forwarded/quoted 旧内容、否定句与修订公告。证据不足保持待澄清，不能猜成确定事实。
- [x] P3-07 用户可以编辑逐个候选；保存编辑仍 Unconfirmed，并使旧比较结果失效/重新计算。需要更换证据或记录用户声明时，UI 明确说明。
- [x] P3-08 长文采用按段/页提取、有限预算与重复事实合并；任何部分分析清楚标注覆盖范围。当前 24,000 字符限制不能被隐藏成“全文已检查”。
- [x] P3-09 用户主动 regenerate、同一 source 重复点击和相同内容 reimport 不产生无限草稿；缓存基于 owner、source revision、extractor/schema version，失败不污染成功结果。
- [x] P3-10 所有事实候选交给 Phase 2 服务；既有 action draft 路径保持独立。对没有适合五类事实的资料，保留 general knowledge summary 流程。

**验收：** Gmail / Calendar / PDF / DOCX / TXT / note 的合成样例都能生成正确来源的候选或明确的 0-fact / failure 结果；无 approval 不新增 confirmed fact；Sync 本身不调用此提取流程。

**2026-11-03 M1 验收边界：** 已完成项按 MEETING_TIME + complete DATE 范围验收。Gmail、Calendar、文件 chunks、note 共用来源契约；time-specific Calendar 明确保留待澄清，不降为 all-day 日期。文件流程和既有 PDF/DOCX/TXT parser 回归通过不等于已实测各种真实文件的 LLM 提取质量。P3-06 中转发/引用、否定、提议、问题、重复会议和缺年份均保守保留澄清；多人负责、项目状态范围等其他事实类型未完成，保持该项开放。

Summary + 0..N candidates 已接原入口；提取候选使用 provisional identity，不能直接批准。只有来源支持的完整日期断言会交给 Phase 2 pending service；缺值/语义不确定者保留在 extraction result，不伪造日期以满足数据库。Edit 保存仍未确认，需重新验证。Phase 4 已接入与原候选关联的 resolved proposal，保留其既有 EDIT/review 历史，不原地改写 identity。详见 [Phase 3 implementation and limits](docs/plans/PHASE3_SELECTED_SOURCE_EXTRACTION.md) 和 [Phase 4 M1](docs/plans/PHASE4_M1_CONFLICT_REVIEW.md)。

## Phase 4 身份匹配、五类比较和冲突审核

**目的：** 形成从 source 到可解释的版本化 memory 的完整用户流程。

- [ ] P4-01 **部分完成：** owner + project 内明确选择已有 identity 或输入 canonical meeting/occurrence，规范化 exact key 并在事务中复用。已审核 alias 和有界语义候选未做；不自动合并相似名称。
- [ ] P4-02 **M1 已完成：** 完整会议日期的 New / Same / Revision / Conflict / Unclear 规则、理由和 rule version。语义变体及 LLM 辅助比较未做。
- [ ] P4-03 **部分完成：** 明确 from/to、来源先后、旧通知与 current 不匹配、问题/否定/引用均有规则。未知来源时间需人工说明；时刻、未来生效区间、周期事件自动解析、集合值和其他类型未做。
- [ ] P4-04 **M1 已完成：** 同源矛盾、同 identity 多个 pending、批准时重查 head/证据，并发 NEW 只有一个 Current。多个相似事实的自动候选发现依赖 P4-01，保持开放。
- [x] P4-05 Workspace → Knowledge 增加 Project facts 的 Needs review / Current / History、关系/项目筛选、搜索和分页；默认关闭的开发功能。
- [x] P4-06 M1 Detail 展示项目/identity、current/proposed、理由、双方证据和时间；支持重新匹配项目/会议，保留原候选历史。
- [x] P4-07 M1 提供 approve、save edit、reject、keep unresolved。可人工解决的 Conflict / Unclear 需明确理由；证据不支持的候选不能靠理由绕过验证。
- [x] P4-08 History 显示每版值、前驱、证据、reviewer、时间和决定，旧版本标 Superseded；证据和审核历史分别分页。
- [x] P4-09 Dashboard / Reviews 从真实记录计算 fact reviews/unresolved；写入使相关缓存失效，不因页面导航自动 sync。
- [x] P4-10 stale head/source/revision 返回可解释拒绝；UI 保留未保存内容并支持 reload / compare again。历史匹配失效可返回原候选重新处理。
- [x] P4-11 旧提取/确认入口的新 summary 明确为 CONTEXT，不创建 accepted FactVersion；历史已确认 Knowledge 不被静默转换为原子事实。

以上勾选按 **M1 date-only 开发范围**完成，不代表完整 report 范围已交付。规则矩阵、迁移边界和演示步骤见 [Phase 4 M1 implementation](docs/plans/PHASE4_M1_CONFLICT_REVIEW.md)。

**验收：** 五类关系各有可复现例子；New / Revision / Conflict / Unclear 均不自动改变 current；Same 不增加版本；身份错误可修正；批准替换后旧证据/版本仍可查看；取消外部操作不会调用 Google 写接口。

**用户可演示的第一个目标：** UAT = 17 Sep → email 明确改 18 Sep → review → V2 current + V1 history；另一个来源写 20 Sep 则显示 unresolved conflict，等待用户决定。

## Phase 5 接入 Copilot、Search、Brief 和派生工作

**目的：** 冲突不能只出现在一个 Knowledge 页面；实际回答和后续工作要使用正确的 memory 状态。

- [ ] P5-01 建立所有消费者共用的 owner-scoped retrieval contract，区分 current accepted facts、superseded history、pending/unresolved proposals、legacy/general context 和 raw sources。
- [ ] P5-02 当前事实问题优先取 relevant current facts；存在相关 unresolved evidence 时明确提示。没有 current fact 但 raw source 有可支持内容时，仍可回答“该来源写明……”并标为尚未确认；不能把 raw 内容称为 accepted truth。连来源也不支持时明确说明证据不足。
- [ ] P5-03 问历史/原始邮件时允许检索相应版本与 sources，并标清状态；不以“current-only”把 FR-06 原始来源搜索和历史查询删掉。
- [ ] P5-04 以 query relevance 选取有界候选，替换目前“最新五条 confirmed knowledge/email 即 context”的限制。测旧但相关的事实能否被找回。
- [ ] P5-05 核实并补齐 keyword + semantic retrieval 的覆盖范围；当前 document vector 能力不等于全部 source 都有 semantic search。遵守 owner、项目和敏感资料过滤。
- [ ] P5-06 Citation 固定到所用 fact version 和 source revision/passage；必要的 conflict warning 不依赖 LLM 自己记得生成。引用存在不等于 claim 有支持。
- [ ] P5-07 保持 active Dashboard 的 Today's brief 为现有本地计算路径，更新其 owner-scoped counts 与 conflict/current 指示。旧 AI `/brief/daily` 如继续保留，须有界、owner-scoped、区分 current/history，并保存 source/version manifest 或 context hash；过期时标明并可再生成，不能静默重写历史 brief。
- [ ] P5-08 Action suggestions / Email drafts / Calendar previews 记录其所依据的 accepted version。事实被替换或 proposal 被拒绝时标记依赖过期、撤销未接受建议或要求重审，不自动修改已接受任务/Google event。
- [ ] P5-09 Copilot confirm 绑定 server-stored suggestion/artifact ID、版本和证据，而非只相信客户端回传 title/body；不能把任意伪造输出当成经过证据审核的建议。

**验收：** 对“当前 UAT 日期”“原来的日期”“新邮件说什么”产生状态正确且有支持的不同回答；unresolved conflict 可见；别人的 memory 永不进入 context；已过期的草稿/操作不能继续沿用旧批准。

## Phase 6 补齐其余 report 行为和操作追踪

**目的：** 完成全文 scope，而不把 conflict 做完就当成 report 全部完成。优先处理审计中有证据的缺口。

- [ ] P6-01 manual note 提供 edit（当前缺少更新 API），并创建 source revision；旧 evidence 仍指向当时版本；未接受提议重新分析/失效。
- [ ] P6-02 验证 sign-in、connect/disconnect、sync ranges、last successful sync、逐连接失败/重试。保持用户已选择的范围，不因页面跳转全量刷新。
- [ ] P6-03 文件上传、inspect、process、delete、错误恢复及 PDF/DOCX/TXT 支持回归。核实生产持久化存储；repo 配置中的本地路径不能证明 Render 重启后文件仍在。
- [ ] P6-04 Action suggestion、acceptance 和 user-created action 分开；确认后可 edit / complete / remove，source/fact evidence 可打开，依赖被拒绝或变旧时清楚显示。
- [ ] P6-05 Email draft 的 recipients/subject/body 可编辑并精确绑定批准版本。local edit 与 provider draft 不能分叉；Send 必须是单独明确点击。
- [ ] P6-06 Calendar create/update/delete 预览显示事件 identity、all-day/dateTime、timezone、旧/新内容；批准绑定确切变更，并检查 Google 端并发修改。
- [ ] P6-07 为 Gmail / Calendar 补齐操作记录职责：reviewed payload、approval time、request/idempotency ID、actor、provider ref、result / error / uncertain outcome。可复用/扩展现有表，不必强制采用 report 的概念类名。
- [ ] P6-08 测 provider success / DB commit failure、timeout after submission、重复点击/重试、Google 端变化、token expiry / revoked access。结果不明先 reconciliation，不能把 unknown 当失败就盲发第二次。
- [ ] P6-09 将 fact/source/proposal/review/external result 串联到可检查的 timeline，而非只保留“item no longer available”的 activity 文本。
- [ ] P6-10 确认 deletion / retention / privacy 清理覆盖新增证据、版本文本、embeddings、AI logs；reimport 不因本地 UUID 改变而自动形成新版本或重复任务。

**验收：** Gap matrix 的所有部分实现项都有对应任务和实际验证记录；Gmail / Calendar 精确批准、取消、失败和重试都符合 Tables 3.5–3.9；移除本地 Calendar source 与删除 Google event 的按钮及后果明确区分。

## Phase 7 评估和整体验证

- [ ] P7-01 基于 Phase 1 的预期样例建立版本化 synthetic/permitted dataset。建议起点为约 100 个案例，覆盖五类关系、五类事实、四类来源及 no-fact/invalid/failure；数量可调整，不能据小样本声称普遍有效。
- [ ] P7-02 将开发/调参样例与冻结的 holdout 分开；记录原文、project/identity/value、时序、预期关系、证据、审核决定和预期状态。
- [ ] P7-03 独立检查标签歧义并保留判定说明。若只有作者标注/试用，如实说明，不虚构参与者或 inter-rater agreement。
- [ ] P7-04 测 extraction/value accuracy、identity matching、逐 claim evidence support、per-class precision/recall/F1 和 confusion matrix；分别报告误报、漏报与 Unclear/abstention 覆盖率。
- [ ] P7-05 比较 rule-only 与 hybrid；预算/时间允许时加入 LLM-only。三者使用同一冻结测试集和相同 source evidence / candidate facts / identity candidates，先做受控 comparison-stage 对照，再单列 end-to-end extraction 结果。不能给 hybrid 正确人工身份却让 LLM-only 从无关全集检索。记录模型/提示词/schema/normalization version、输入覆盖、latency、token usage / cost、重试和失败。mock 与 live 实验分开；不预设 hybrid 一定胜出。
- [ ] P7-06 对 current / history / unresolved / raw-source 查询检查 Copilot 和 citations；包含旧但相关资料、错误引用、缺证据、prompt injection 型来源和跨用户测试。
- [ ] P7-07 在 PostgreSQL 测 uniqueness、并发 double approve、stale head、rollback、idempotency、migration/backfill/rollback；测试 AI failure 不损坏 raw sources 或 confirmed facts。
- [ ] P7-08 按 report §3.6 对 deadline lookup、revised UAT meeting、weekly follow-up 比较 native workflow 与 KnowAct：正确完成时间、步骤、app switches、结果正确性及 review effort。尽量交叉任务顺序，记录学习偏差。
- [ ] P7-09 在固定规模 dataset 上定性能/成本目标后测量；不能测试后挑容易达到的阈值。先不声称 SLA 或 productivity improvement。
- [ ] P7-10 记录 Chrome / Edge 版本及实际桌面流程；模拟 failure/cancel 与 live test-account approved writes 分开记录。

**验收：** 每个 report requirement 都有输入、预期、实际、结果与限制；控制类测试要求不出现跨用户访问、未批准写入、重复外部效果或两个 current heads。AI 质量/速度目标在跑正式 holdout 前确定，失败如实记录并修复或标注限制。

## Phase 8 发布与 report / diagrams 同步

- [ ] P8-01 所有相关回归、frontend build/typecheck 和 schema checks 通过；核对 migration head、backfill inventory 与可恢复备份，准备 rollout / rollback steps。
- [ ] P8-02 在独立测试数据库/测试账号验证新旧数据共存；先 additive schema，再按可回退步骤启用，不盲目在生产重建表。
- [ ] P8-03 到发布时才进行授权范围内的 GitHub push、Render / Vercel 部署与 Neon migration；是否自动部署要当时验证，不能把 push 成功当部署成功。
- [ ] P8-04 在部署后的 KnowAct 走完整浏览器场景，验证 auth、selected-source extraction、conflict review/current/history、Copilot citations、同步、文件持久化与操作结果。
- [ ] P8-05 从最终代码重做 class diagram / ERD / activity / sequence，标清 implementation vs conceptual design，不沿用旧物理图声称是完整 conflict-aware schema。
- [ ] P8-06 用户需要时另建 report 更新副本/implementation evidence appendix，保留本次已完成的原 report；使用实际结果，不回写虚构准确率/效率。
- [ ] P8-07 完成 FR / NFR traceability、演示数据、已知限制和下次继续的 checkpoint。

**验收：** 代码、迁移、运行中的网站、图与验证记录一致；报告中的核心范围均有实现或明确未完成项。只有实际验证达标后才把阶段标为完成。

## 在相应阶段前确定的决定

| ID | 决定 | 建议与边界 | 何时确认 |
|---|---|---|---|
| D1 | 自动 vs 手动触发 | **已由用户确定：仅选定 source → Add to Knowledge。** 自动批量提取留作以后单独讨论 | 已确认 |
| D2 | 时间预算 | **用户暂不设截止日期。** 按阶段交付，不以无限期工作代替可验收目标 | 已确认 |
| D3 | 删除与历史/隐私 | **用户已确认双层规则**：Remove 删除本地 source 并告知保留最少已批准 evidence；Purge 清除相关内容副本/evidence/embeddings/AI logs，历史标 evidence removed，不改 Google 端资料 | 已确认；Phase 2/6 实现 |
| D4 | 旧 Knowledge 转换 | 建议保留为 legacy/general context；可恢复 owner 才回填；重新提取的 structured facts 需用户 review | Phase 1 数据设计 |
| D5 | 无项目/无五类事实的内容 | 建议保留一般摘要；需要比较时明确选择项目，不能跨所有项目推断 | Phase 1/3 |
| D6 | 评估规模/质量目标 | 建议先约 100 个合成/获准案例；正式目标、参与者与 live API budget 待评估设计时确定 | Phase 1 起草，Phase 7 冻结 |

D4–D6 是计划内设计事项，在对应阶段给出具体方案再讨论。D3 已确认，建立 evidence 表前落实明确的内容保留/清除关联和 UI 文案。

## 每次继续时的工作记录

开始前读取本文件、gap audit、上次 checkpoint、`git status` 和最新代码；report / 代码若变更，先修正差距再执行。不要因为旧会话说完成而跳过验收。

每个实际开发批次追加：

```text
Date / commit:
Phase / task IDs:
Implemented:
Files / migrations:
Tests and actual results:
Live verification boundary:
Accepted decisions / scope changes:
Remaining issues:
Next concrete task:
```

### Phase 1 完成 checkpoint（历史记录）

- 日期 / 代码：2026-11-02；基线仍为 `2bd0478`，本轮改动尚未提交/push。
- 当前阶段：Phase 0、Phase 1 本地完成。Phase 2 尚未开始；M1 尚未完成，不宣称已有 Conflict Detection。
- 完成任务：P1-01–P1-09。事实/状态/项目/删除契约见 [Phase 1 fact contract](docs/plans/PHASE1_FACT_CONTRACT.md)。
- 功能代码：新增个人 owner 字段与统一权限 helper；Core 路由、Workspace summary/search/list/provenance、Copilot/Brief/Email context 和 Calendar action 查询按个人隔离。SourceProposal summary 归为 CONTEXT；Knowledge pending/approved/rejected 终态保护与幂等审核。
- Gmail：materialized draft 编辑同步到 provider；编辑使旧批准失效；Send 前同步已保存的 recipients/subject/body，更新失败不发送。UI 允许编辑已建立草稿；存在未保存改动时禁止批准/建立草稿/发送，并撤销旧发送确认。
- 迁移：新增 `0014_personal_workspace_owners`。无法证明 owner 的旧数据隔离；不根据 assignee 或当前用户猜测。规则与回退见 [Migration runbook](docs/plans/PHASE1_MIGRATION_RUNBOOK.md)。
- 本地验证：**469 backend tests passed**（含真实独立 PostgreSQL 0014 upgrade/backfill/downgrade/re-upgrade；其余为 SQLite/mock/fake providers），**92 frontend tests passed**，`npm run build` 通过，代码 diff whitespace check 通过。
- 迁移合成样例：前后均为 6 Knowledge / 5 Action / 2 Entity / 1 Source；有依据的归属被回填，矛盾/缺失依据保留 NULL，原始总行数不减少。没有读取生产资料作这些计数。
- 测试预期：`backend/tests/fixtures/conflict_mvp_cases.json` 有 29 个开发案例，覆盖关系、review、跨用户/项目、时间精度、证据、并发/过期和删除。**这些不是检测算法的通过成绩，也不是冻结的评估 holdout。**
- 未验证/未执行：完整历史 PostgreSQL migration chain、Neon 当前 schema/data、生产数据回填、live AI 品质、真实 Google writes、浏览器端到端流程、Render/Vercel 部署。新增 0014 未应用到 Neon；新版后端依赖这些列，不能直接对旧 schema 运行。
- 保留边界：没有实现 Purge UI/内容清除引擎，没有加入 FactIdentity/FactVersion 或 Conflict UI；精确版本绑定、provider 外部改动和 unknown outcome reconciliation 按后续阶段处理。旧 general context/AI history 不因 owner 回填而获得语义正确性证明。
- 已确认决定：无固定截止日；selected-source extraction first；Summary 与逐项 Fact 独立批准；M1 先做一个项目的 UAT date Revision；可选公平 LLM-only baseline；D3 双层删除。
- 下一具体任务：Phase 2 先列出 D3 涉及的内容存储与 lineage，再实现 M1 最小 FactIdentity / accepted version / proposal / evidence / review 事务。用可控候选验证 17 Sep → 18 Sep、Reject/Edit 不改旧值、stale head/source 拒绝及重复批准幂等，再接入选定 Email 提取和 review UI。

### Phase 2 M1 完成 checkpoint（历史记录）

- 日期 / 代码：2026-11-03；基线仍为 `2bd0478`，Phase 1/2 改动均未提交或 push；保留已有无关文件。
- 完成范围：P2-01–07、P2-09–10 的 M1 基础。P2-08 保持部分完成，不能把内部事实层 redaction 当成整个 workspace 的 Purge。
- 存储：新增 `0015_structured_fact_memory`，七张表负责 identity、source snapshot、evidence、proposal、accepted version、evidence link、review。复合外键隔离 owner/project/identity，唯一索引保证一个 Current，触发器保护身份、已批准值和审核历史。
- 审核：统一 FactMemoryService；内部 controlled propose/compare；Edit 使 comparison 失效；Approve 绑定 proposal revision + current head + source fingerprint；New/Revision 建版本，Same 只连证据；Reject/Keep unresolved 保留已接受值。Keep unresolved 也使另一个旧页面的 Approve 过期。
- 事务：PostgreSQL origin advisory lock + identity/proposal/source row locks；同一事务保存 supersede/new version/evidence/review/audit；savepoint 避免失败后留下部分版本；幂等 key 返回原审核记录，不产生第二版。
- 证据：完整内容指纹、精确 passage/span/chunk locator，Gmail alias/reconnect/reimport 的稳定 origin；超过 24,000 字符后的文件改动也能使旧提议过期。未知 authored time 不用本地上传/录入时间冒充。
- 边界：事实层不自动建立 Action，不发送邮件，不修改 Calendar，不改一般 Knowledge 的批准含义。新 `/api/dev/fact-memory` 默认关闭，PRODUCTION 即使打开 flag 也返回 404；没有对外 propose/comparison API。
- 删除：Source/Gmail/Calendar/file 移除接上 snapshot unavailable；内部 `purge_fact_origin` 清除同 origin 的事实证据/提议/审核文本及无独立支持的值，保留版本链。待批准快照最小化与完整 legacy/raw/file/log/cache Purge 仍待 Phase 6。
- 验证：**完整 backend suite 530 passed**（含真实本地 PostgreSQL 0014/0015 与并发测试）；末次修改/新增测试后 **64 项 Phase 2 专项 passed**。后者包含前者的相关测试及 3 项新增测试，不能把两个数字相加作为独立用例数。编译检查与 tracked diff whitespace check 通过。现有 deprecation/test JWT 警告不构成失败。
- 实测场景：Email 中 UAT 17 Sep → 18 Sep；V1 Superseded/V2 Current；同证据及删除后重导入不增加版本/重复 support；Edit/Reject/Unresolved、旧 head/source/revision、跨用户/项目、来源缺失、source 敏感/排除、假证据、audit 故障回滚；真实 PG 两个 NEW/REVISION 并发请求只能一个成功，相同并发请求返回同一结果。
- 未执行：Neon schema migration/生产 inventory、live OpenAI、Google writes、前端新 Conflict 页面、浏览器端到端、Render/Vercel 部署。本轮未改前端，所以没有重新运行 Phase 1 已通过的前端 suite/build。0014/0015 都未应用到 Neon，新后端不能直接用于旧 schema。
- 结论：**版本和人工审核基础可用于接入 M1；尚不是自动 Conflict Detection，也不是完整 Conflict MVP。** 规则只对受控 complete-date 候选作窄门检查，不能拿这些测试当作 AI extraction/relationship accuracy。
- 下一具体任务：Phase 3 的 M1 小批次：用户选定 Email + 明确 Project → General Summary + 0..N 独立的 meeting-date proposals；接已有 owner/source/fingerprint/evidence/review 服务，先实现 schema-validation、grounding、失败状态和去重，再在 Phase 4 接审核 UI 与比较。避免在可见 MVP 前扩展所有 fact types。

### Phase 3 M1 提取与候选编辑 checkpoint（历史记录）

- 日期 / 代码：2026-11-03；开始时 Phase 1/2 已提交为 `ad6ca1b`。本轮 Phase 3 未提交、push 或部署；保留用户已有 `collected_code.txt`、artifacts、临时文件等无关改动。
- 实现：扩展原 Add to Knowledge 入口为 general summary 或明确 PROJECT + summary/facts；支持用户手动创建项目。严格 provider schema 返回叙述摘要与 0..N 个 date-only MEETING_TIME 候选，记录原值、完整规范日期、modality、previous value/change cue、来源上下文和证据。
- 审核边界：摘要可单独编辑/批准为 CONTEXT，并带所选 project；不生成 FactVersion、不批准候选。完整且有原文支持的候选交 Phase 2 pending service，使用 unverified provisional identity；正式比较/批准被 guard 阻止，等待 Phase 4 解析身份。用户可编辑完整日期与精确 passage，保持 unconfirmed 并记录需重新验证。
- 保守处理：未知年份、问题、提议、否定、转发/引用、未明确 occurrence 的重复会议、time-specific Calendar 都保留澄清。其他事实类型尚不提取；不能把合成案例通过率当作五类关系检测或真实 LLM 准确率。
- 缓存 / 失败：owner/project/origin/full fingerprint/extractor/schema/provider/model 隔离缓存；request receipts 覆盖缓存命中并绑定意图；同请求并发只调用一次 provider；同 key 不同来源返回 409。失败保留无原文异常的 FAILED receipt，0 facts 是成功；重试明确失败用新 key，未知网络结果保留原 key。重导入可复用分析输出但建立新的本地 pending 来源/候选，不复活已删除原文或审批。
- 范围 / 成本：最多 3 个 8,000 字符分段，总预算不超过 24,000；显示实际 ranges/覆盖量/partial。完整指纹仍覆盖省略部分，文件尾部改动会使旧审批过期。每 owner/project/origin/content/extractor revision 最多 3 次生成尝试（含失败/重导入）；这是开发阶段成本上限，不代表正式性能/成本评估。
- 隐私 / 权限：缓存和重试前重新检查 owner/project/source eligibility；生成期间来源变化/删除不发布旧提议。来源后来变为 highly sensitive 时重新要求明确确认，并把对未变缓存内容的新确认保留到摘要/证据，编辑与摘要审批不会反复卡在旧确认状态。
- 存储：新增 `0016_fact_extraction_runs`，FactExtractionRun / FactExtractionRequest 和 FactProposal.extraction_context；最小 snapshot 让 0-fact/failed 结果也有 purge origin。内部 fact-layer redaction 清除新 output/coverage/context；完整 D3 与 general-summary purge 并发协调仍待 Phase 6。
- 前端：项目选择/创建、覆盖范围、0-fact/澄清/候选状态、独立日期与证据编辑、摘要单独批准；候选修改使相关缓存失效，不覆盖未保存摘要。Enter 不会误触摘要批准；失败重试与 evidence removed 显示有回归测试。
- 验证：**完整 backend 584 passed**（真实 PostgreSQL 0014/0015/0016、并发与回滚）；之后补充边界/隐私检查的 **82 项专项 passed**。后者与全量重叠并包含后加测试，不能相加当成独立用例总数。**frontend 104 passed**，`npm run build` 通过；编译、Alembic 单一 head 与 tracked diff whitespace checks 通过。
- 未执行 / 限制：没有 live OpenAI 质量/延迟/费用验证，没有浏览器端到端，没有 Google writes，没有生产 schema/inventory 检查，没有 Neon 迁移/Render/Vercel 部署。开发入口默认关闭，PRODUCTION 强制关闭；0016 未在本轮应用到 Neon，运行本 checkout 前需验证目标数据库有完整 schema。
- 下一具体任务：Phase 4 的 M1 身份解析与审核：先让用户确认 UAT occurrence/identity，把候选解析为有 lineage 的新 proposal；取同 owner/project 的已批准当前值，展示 New/Same/Revision/Conflict/Unclear 的理由与双方证据，先打通 17 Sep → 18 Sep 的可见版本审批。不要重写已有 candidate identity/EDIT 历史，也不要先扩展全部事实类型拖延 M1。

### 当前 checkpoint：Phase 4 M1 冲突审核链路

- 日期 / 代码：2026-11-04；基线 `ad6ca1b`。Phase 3/4 改动保留在本地，未提交、push 或部署；无关用户文件保持原样。
- 已实现：选定 source → summary + 独立候选 → 用户明确匹配 project/meeting/occurrence → 服务器规则比较 → 人工审核 → versioned memory。17 Sep → 明确较新 18 Sep 的 Email 流程通过真实 HTTP 路由测试；20 Sep 的不兼容来源可保留 unresolved，Current 不变。Summary approval 不确认 facts。
- 身份：新增 FactResolution，原 provisional identity/EDIT 历史不重写；rematch 建新的关联 proposal，使旧匹配失效。已批准关联禁止重新解释。请求幂等、owner/project 隔离及审批/重匹配竞争有 PostgreSQL 验证。
- 比较：完整日期 MEETING_TIME 的五类规则，记录 rule_version/reasons；明确替换必须对应现有 Current 且有可靠较新来源时间。延迟旧通知、未知/同时来源时间需人工选择依据；问题、否定、取消、引用、错误日期证据不能直接批准。Same 只增加审核过的支持证据。
- UI：Knowledge 的 General knowledge / Project facts、Needs review / Current / History、筛选搜索分页、current/proposed 对照、逐项编辑/批准/拒绝/保留未决、版本与证据/审核历史。Dashboard 和 Reviews 接事实待办；Source 卡片显示 active resolution 的值、证据和状态，不篡改原候选。
- 防旧状态：批准重新验证 source fingerprint、proposal revision、current head、有效匹配和比较；UI 保留未保存编辑，提供重新比较和失效匹配恢复。修正取消语句经编辑绕过提取验证的问题，并加回归用例。
- 迁移：新增 `0017_fact_resolution`，比较 JSON、不可变 resolution lineage、content-free comparison request receipts。真实本地 PostgreSQL 验证 upgrade/downgrade/re-upgrade、复合 FK、唯一 active match 和并发事务；0017 是单一 Alembic head。
- 验证：完整 backend **625 passed**（含真实独立 PostgreSQL 测试）；最后 Source 展示修正后，extraction + HTTP workflow 专项 **33 passed**。frontend 完整 **113 passed**；另补 Source 审核显示与 Dashboard 计数回归后，相关 **20 passed**，随后 production build 再次通过。专项与全量有重叠，不相加称作总成绩。
- 未验证：浏览器工具无法连接本机 browser/native host，故没有真实页面视觉或登录后端到端验证；未调用 live OpenAI、未写 Google、未访问/迁移 Neon、未部署 Render/Vercel。合成数据与 fake provider 测试不等于真实 LLM 准确率。
- 剩余：完整 Phase 4 的语义身份候选/alias、LLM 辅助比较、其他事实类型、复杂时间/集合规则；完整 D3 仍待 Phase 6，Copilot/Search 尚未读取新 FactVersion（Phase 5）。
- 下一具体任务：先在隔离开发环境演示 M1 页面并核对实际 AI 提取；按 report 继续 P4-01–04 的扩展。不要把 M1 本地完成写成完整 Conflict Feature 或已上线，也不要自动跳到 Phase 5。
