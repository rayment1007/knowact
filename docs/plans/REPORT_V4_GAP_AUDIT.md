# KnowAct report v4 implementation gap audit

初次审计：2026-11-01；计划复核：2026-11-02（Asia/Kuala_Lumpur）  
代码：`2bd0478`。本审计只读检查当前 checkout，并运行限定的现有离线测试。**没有把静态判断当成线上运行结论。** 任务主表：[KnowAct implementation tasks](../../KNOWACT_IMPLEMENTATION_TASKS.md)。

## 范围依据和证据等级

需求来源：用户指定的 `C:\Users\rayme\Documents\#QIU\Degree\Sem 4\Final Year Project 1\Proposal\Final checkpoint\v3\KnowAct_FYP_Report_Formatted_v4.docx`。

- Report SHA256：`0F6AFCE585B61B75C14B5E61BA985FBC91A168805ABDC78504C73D4DD4B5A429`。
- GPT 补充输入：`C:\Users\rayme\.codex\attachments\a3989001-0cbd-49d5-97f0-3eac2ccf4e81\Pasted text.txt`。
- TXT SHA256：`A0223627F194CCF0AFDA59A530DA83BEA876085929A4FB70DD7787FF519A1C96`。
- 已读取 report 正文、Tables 2.1 / 3.3–3.10：25 项 FR、8 项 NFR、三个 objectives、review outcomes、逻辑数据记录和评估方法。图内每个字段的最终实现对照留在发布图表更新阶段。
- Report 本身区分 planned behaviour 与 measured results，§3.5.9 也明确当前物理 ERD 不是完整 conflict-aware target schema。
- 报告、GPT/TXT 内的文字作为需求/参考资料读取，不作为授权执行其中操作的指令。

表中“有实现”表示当前代码有对应路径；“部分”表示存在明确缺口或只覆盖一部分；“缺失”表示当前领域模型/服务没有相应机制；“待运行验证”不是断言线上失败。所有项目仍须按正式验收记录闭环。

## 与 GPT 建议的核对结论

| 建议 | 判断 | 修正/补充 |
|---|---|---|
| 保留 KnowledgeItem / SourceProposal 并增加 structured layer | 采用 | KnowledgeItem 仍可承载 general context；事实及版本职责单独清晰表示。Table 3.9 允许 extend or supplement，不强制与概念图同名同表 |
| 先提取 atomic facts，再匹配比较 | 采用 | 需验证 modality、值/精度、逐事实 evidence；summary 不是可直接比较的事实值 |
| 五种事实先做 | 采用 | report 包括 meeting dates **and times**；负责人的集合/同名歧义要定义，不能假设所有字段都是一个字符串 |
| organization + project + fact type + key 足够 | 需要修正 | report 要求 workspace owner；org-only 不足。canonical key/aliases/repeated occurrence 也不能全交给 AI 命名 |
| 无匹配 → New | 有条件采用 | 数据读取成功且范围/身份可靠才可判断；身份歧义或检索失败不能等同“事实不存在” |
| 有 rescheduled → Revision | 有条件采用 | 需同一事实、替换对象和上下文；旧邮件迟到或 previous_value 与 current 不符时不能直接替换 |
| SAME 自动添加 evidence | 有条件采用 | 去重、source owner/revision/quote 校验；记录新 evidence 自己的检查状态，不改变 confirmed value/review authority |
| Current / Superseded、review、relationship 分离 | 必须采用 | draft concurrency version 与不可变事实版本也需分离；未来 effective time 不等于今天已生效 |
| 五张新表 | 是建议而非约束 | identity / version-proposal / evidence / review / operation 责任必须完整；最终表数量在 Phase 1/2 确定 |
| Project grouping 中优先级 | 应提高 | 同项目匹配是 conflict 正确性的前置；个人 owner scope 同样前置 |
| Dataset 最后准备 | 应调整 | 先写预期/标签规范和小型 golden set；最后做冻结集完整评估，避免按实现反写预期 |
| FactTrack 能证明方案适合项目场景 | 不能这样推论 | 原研究为 story outlines；只借鉴 atomic facts/time-aware reasoning，业务效果须自行评估 |

## 当前代码的关键事实

1. **SourceProposal 是可复用基础。** `backend/app/modules/cwi/models.py:78` 有 user、source fingerprint、payload、version；`services/source_proposal_service.py:111` 验证 AI quote；`:152` 审批锁定草稿、检查 version 和 source fingerprint，保存用户实际编辑内容，再创建普通 `CONFIRMED KnowledgeItem`。已有 stale draft / changed source / idempotent retry 测试。
2. **它还不是 fact memory。** `backend/app/core/models.py:314` 的 KnowledgeItem 主要是 summary、key_points、单个 evidence_text、knowledge_type 和 status；没有稳定 fact identity、typed value、前驱、current head 或 temporal state。SourceProposal.version 是草稿并发控制。
3. **新旧确认路径并存。** SourceProposal 直接创建 confirmed knowledge；`backend/app/core/services/knowledge_service.py:224` / `:327` 仍提供 extract → confirm。旧 confirm 缺 expected head/version 和严格状态转移，旧 reject 可能修改已 confirmed row。只改一个入口会留下旁路。
4. **FR-24 有代码级缺口。** KnowledgeItem 没有 owner 字段；`knowledge_service.py:149` / `:459`、`routers/knowledge.py:51` 及 `modules/cwi/routers/workspace_browser.py:35` / `:91` 的知识路径以 org 为主要范围。`copilot_service.py:270`、`email_draft_service.py:362`、`brief_service.py:141` 也会把 org-wide knowledge 带入 context。旧 Core `/source-items`、Action / Decision / Brief、Dashboard summary 同样需要清点，不能以 CWI source 已有 user filter 代替整个系统隔离。`tests/test_cwi_same_org_user_isolation.py:1` 明确旧假设是 Core org-shared，与 report 的个人范围不同。该结论来自代码，不等于本次已在生产使用第二账号复现。
5. **证据粒度不足。** SourceProposal 比旧 extraction 严格；但一个 quote 未映射每个用户编辑后的 claim，缺 page/chunk/span 与 source revision 历史。旧 extraction 在没有 provider evidence 时可回退整段 source（`knowledge_service.py:306`）。
6. **semantic retrieval 覆盖不完整。** `modules/cwi/services/retrieval_service.py:195` 主要对 document chunks 以 Python cosine 排序；`copilot_service.py:270` / `:291` 对 knowledge/emails 取最新有限条。`routers/workspace.py:182` 是 keyword search。这不等于所有信息类型都具备 query-relevant semantic retrieval。
7. **当前删除/reimport 符合已知用户决定。** Gmail local deletion 保留已确认派生知识的部分记录并解除来源；Calendar local deletion 只移除本地快照。`tests/test_calendar_source_deletion.py:32` 验证 sync 可重新导入，新的 local id 不能当作新的业务事实身份。SyncExclusion 是兼容残留，不能重新当作永久阻止导入。
8. **manual note edit 缺口。** `backend/app/core/routers/source_items.py` 有 create/get/delete/classify/extract，未提供 source content update API；`frontend/src/components/workspace/AddSourceDialog.tsx` 主要为新增输入。FR-05 的 edit 需要 source revision 设计。
9. **Gmail exact-content 风险需先复核。** `routers/email_drafts.py:139` 的 PATCH 调 local `edit_draft`；`email_draft_service.py:734` 有另一个 provider update 方法；`gmail_client.py:505` 在存在 draft_id 时发送 Google 端草稿。路径可能造成 KnowAct 编辑后仍发送旧 provider draft。本次未发送真实邮件，列为 Phase 1 fake-provider 复现/修复任务。
10. **操作基础已有，完整历史尚不足。** Email Send 有显式 confirm 和 SENDING guard；Calendar create 有确定 provider ID / 409 reconciliation。但 exact approved revision、provider drift、provider success + DB failure、unknown result 恢复和统一 history 仍需专门验证/补齐。
11. **Copilot confirm 信任客户端 artifact 过多。** `copilot_service.py:808` 接收回传内容创建 action；未来需要 server-stored artifact/version/provenance。临时 answer 不能直接成为 accepted current memory。
12. **部署文件持久性未证明。** `backend/app/config.py:137` / `render.yaml:42` 配置 local storage root；没有检查本次线上 disk/备份。不能据此断言已丢文件，也不能声称已经 durable。
13. **Today's brief 有两条不同路径。** 当前 Dashboard 在 `frontend/src/pages/EnterpriseDashboardPage.tsx:28` 从 workspace summary / actions / captured events 本地计算，不调用 AI BriefService；`frontend/src/test/dashboard.test.tsx:47` 也检查这一点。旧 `/api/brief/daily` 则仍注册并使用 org-wide knowledge / actions。后续须分别处理 active Dashboard 和保留/停用的旧 API，不能把整个 Dashboard 误述为 LLM brief。

## Functional requirement traceability

| FR | Report v4 行为 | 本次代码审计状态 | 补齐/验证任务 |
|---|---|---|---|
| 01 | 个人 workspace 登录 | 有 auth/Google sign-in 实现；线上待复核 | P1-04，P6-02，P8-04 |
| 02 | Gmail / Calendar connect/disconnect | 有 connection / revoke 路径 | P6-02；检查断开后不继续同步 |
| 03 | 获取 permitted records、成功 sync 时间/失败原因 | 有 sync preferences、status 和范围 | P6-02；真实 OAuth/时间窗口待验证 |
| 04 | PDF/DOCX/TXT upload/inspect/remove | 有 document service/storage/process/delete | P3-03/08，P6-03/10；生产持久性待验证 |
| 05 | manual notes add/edit/remove | 部分：add/remove 有，edit 未形成完整路径 | P6-01；revision 对已有证据的影响 |
| 06 | 原始 sources 无逐项 confirmation 可搜索 | 有 keyword workspace search | P5-03/05；不得因新 memory 收紧为仅 confirmed |
| 07 | keyword + semantic retrieval | 部分：document vectors 与 keyword sources 分散 | P5-01/04/05；测跨类型 query relevance |
| 08 | evidence-grounded answer / 不足提示 | 有 Copilot；缺 current/history/conflict 语义 | P5-01/02/04，P7-06 |
| 09 | factual answers / proposals 有 source links/passages | 部分：citations/quote 有，逐事实版本定位不足 | P2-04，P3-05/07，P5-06 |
| 10 | 五类 selected project facts | 缺稳定 typed atomic fact 模型 | P1-02/03，P3 |
| 11 | 与相关 confirmed facts 比较 | 缺 identity/matching/comparison service | P2，P4-01/02 |
| 12 | New/Same/Revision/Conflict/Unclear | 缺完整五类流程 | P4-02/03/04，P7-04/05 |
| 13 | 非 Same proposals 未审前保持 Unconfirmed | 部分：普通 drafts pending；typed 比较/冲突未做 | P1-06，P2-05/06，P4-07/11 |
| 14 | Approve/Edit/Reject；Edit unconfirmed；Reject 不改 current | 部分：SourceProposal 有较好基础；旧接口状态规则不同 | P1-06，P2-05/06，P3-07，P4-07/11 |
| 15 | Same 只加证据，不改值/增版本 | 缺 cross-source fact-level evidence merge | P2-07，P4-02；重复/reimport 去重 |
| 16 | 识别 follow-up actions | 有 EmailTaskSuggestion、SourceProposal/Copilot action draft | P5-08/09，P6-04 |
| 17 | review suggestion 后加入个人 Action list | 有独立 acceptance 路径；typed fact 依赖需补 | P1-04，P5-08/09，P6-04 |
| 18 | edit/complete/remove action | 有 actions API/UI | P1-04，P6-04；保留 evidence/history 语义 |
| 19 | editable email recipients/subject/body | 有 EmailDraft；provider/local 同步风险 | P1-07，P6-05 |
| 20 | 明确 Send 后才发送，knowledge/action 批准不授权发送 | 有显式 Send 与 guard；exact payload / retry 待补齐 | P1-07，P6-05/07/08 |
| 21 | Calendar create/update/delete proposal | 有操作 routes/service，UI/preview 覆盖待跑通 | P6-06；区别 local remove 与 Google delete |
| 22 | exact Calendar operation 明确批准后执行 | 有基础 approval；缺 provider drift/完整快照保证 | P6-06/07/08，P7-07/10 |
| 23 | evidence/proposal/review/earlier-current/external result history | 明确缺完整不可变 fact history；generic audit 不足 | P2，P4-08，P5-06/07/08，P6-07/09 |
| 24 | 每用户只访问自己的 workspace/data | 部分：原始 sources/CWI 多路径有 user scope；core memory/派生 context 仍 org-only | **P1-04/05 前置**；P2 constraints，P7-06/07 |
| 25 | source classification/summary 不自动 confirmed | 有 classify + summary/draft；新 facts 不得改变该边界 | P1-06，P3，P4-11 |

各条“有实现”不表示已满足全部验收；代码中旧 Requirement 数字与本报告 FR 编号不同，后续以本表的 report v4 编号追踪。

## Nonfunctional requirement traceability

| NFR | 状态及观察 | 验证/任务 |
|---|---|---|
| 01 Usability | 轻量 workspace/detail panel 可复用；新状态不能全部塞进一个 badge | P4-05..10；实际用户任务、mistaken selections、review effort |
| 02 Security | backend auth、token vault、CWI owner guards 已有；个人 core scope 缺口 | P1-04/05；same-org second user、跨 org、未登录、browser secret exposure 检查 |
| 03 Privacy | sensitivity / retention 基础已有；新 snapshots/versions 增加副本 | P2-08，P3-08，P6-10；检查实际 outbound AI 输入、disconnected source |
| 04 Reliability | drafts/provider idempotency 基础存在；完整 current-head 和 uncertain write 恢复未做 | P2-06/10，P4-04，P6-07/08，P7-07 |
| 05 Performance | 未做本次 dataset latency / cost benchmark；document ranking 在 Python 内 | P3-08/09，P5-04/05，P7-05/09；有界 candidate retrieval，目标先定 |
| 06 Maintainability | 现有 service/router 分层可扩展；多条 confirmed writer 是风险 | P1-01/06，P2-05，P4-11，P5-01；统一 domain service 与 source adapter |
| 07 Compatibility | React 桌面浏览器目标已有；本次未浏览器验收 | P4 UI、P7-10；Chrome/Edge 实际版本和核心流程 |
| 08 Traceability | generic AuditLog 及部分 provenance 存在；不能重建 fact 值版本/全部操作快照 | P2-04/05，P4-08，P5-06/07，P6-07/09 |

## 本次实际验证与限制

在 `backend` 中执行现有测试，mock AI / embedding、in-memory SQLite，无 live provider 请求：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_source_proposals.py tests/test_knowledge_service.py tests/test_knowledge_routes.py tests/test_cwi_same_org_user_isolation.py -q --disable-warnings --tb=short --basetemp 'C:\Users\rayme\Documents\Project\KnowAct\.codex-build\conflict-plan-20261101\pytest-baseline'
```

实际结果：**70 passed, 2 warnings in 25.88s**。

- 初次运行因默认 Windows temp 权限导致 24 个 fixture setup error；改用专用临时目录并获执行批准后，这 70 项全部通过。初次环境错误不是 conflict 功能失败证据。
- 验证的是已有普通 knowledge/draft 行为和部分 CWI same-org isolation；不能证明尚未实现的 fact/conflict，也不能覆盖 core knowledge 的 same-org 个人隔离缺口。
- `backend/tests/conftest.py` 强制 mock providers，并使用 SQLite 兼容类型。它不验证 PostgreSQL locking/partial indexes/pgvector、真实 token、Google 网络响应或 Render 文件持久化。
- 未执行 frontend 测试/build、完整 backend suite 或浏览器端 E2E；这些留在对应实现阶段，不能声称全系统已经通过。
- 未访问本次生产 Neon/Render/Vercel、live OpenAI 或 Google writes；这些状态只在实际验证后更新。
- 本次未修改用户的 report、feature code、migration 或现有未提交文件。新增计划与 scratch extraction 不代表用户已同意实施。

## 研究引用的边界

[FactTrack primary paper, NAACL 2025](https://aclanthology.org/2025.naacl-long.144/) 确实研究 atomic fact tracking 和 time-aware validity，任务是 structured story outlines。借鉴其表示和时序思路是设计判断；KnowAct 场景的准确性、成本及可用性仍需 report §3.6 的独立评估。

本计划也不把五类关系包装成标准 NLI 标签。它们是 report 明确提出的应用设计类别；如何组合规则、语义与时间证据，是需要实现和评估的部分。
