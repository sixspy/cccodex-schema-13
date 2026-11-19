# cc-schema-lab

CC_Codex 二期（26.11）**数据工程**产品线：**契约换版台**（aiohttp + 原生 JS SPA + SQLite）。

## 一句话骨架（自有，非外仓抄题）

**表契约版本登记 → 破坏性检查 → 换版应用 → 目录对账**

与现有九线正交（不是采集、ETL、同步、解析、编排、摄取、留存、清单、水印）。
不碰外仓常见指纹（Feed 归并 / CDC 游标 / 质检签发 / Slicegate 类仓名）。

## 领域

- **所属领域**：数据工程
- **子方向**：数仓与数据湖（表契约 / 换版闭环）

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | Python 3.12、aiohttp、SQLite |
| 前端 | 原生 JS SPA（全中文），暖杏纸面 + 墨绿强调；顶栏胶囊导航 |
| 测试 | pytest + pytest-aiohttp（本地秒级闭环，优先于 compose） |

## 基线已通主链

1. 表契约版本（表名 / 版本号 / 现行或草稿）
2. 对候选字段做破坏性检查（删列 / 改类型）
3. 换版应用（草稿升现行，旧现行停用）
4. 契约与目录表对账

## 升版审计（schema-13）

**落库形态只留一种：独立审计快照表 `upgrade_audits`。** 不物化外部审计文件、不挂契约外键双套凭证；
审计队列、时间线详情、升版卡片、合规进度四处读的都是这张表。

- **同源同事务**：`apply_contract` 用 `BEGIN IMMEDIATE` 先占写锁，同一事务内写审计快照 → 停旧现行
  → 升新版 → 写目录 → 写 `apply_runs.audit_id`。审计写不出就整笔回滚，版本仍是草稿。
- **缺审计即拦**：`apply_runs.audit_id` 由触发器强制非空；`upgrade_audits` 有 UPDATE/DELETE 拒写触发器。
- **并发互斥**：两名审计员同时点升版，后到者拿到写锁时已读到「现行」，直接 409，不会双写。
- **凭证形态**：快照做规范化 JSON 后取 SHA-256，凭证号 `AUD-<指纹前12位>`，落库位置显示为
  `upgrade_audits#<id>`；快照里含操作人、前后版本、字段全量、应用时间。
- **合规复验**：逐条升版记录回读快照并重算指纹，缺审计 / 指纹不符 / 与升版不同源 都判不合规。

预留切口（未做，供后续功能题）：双写迁移 vs 锁表切流、跨表依赖冻结、回滚窗口等。

## 本地

```powershell
cd claude-workspace\cc-schema-lab
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest
python run.py
```

http://127.0.0.1:7100/login · **admin / 123456**

## Docker

端口 **7100**。冒烟：`python ..\scripts\_smoke_lab_access.py --dir .`
