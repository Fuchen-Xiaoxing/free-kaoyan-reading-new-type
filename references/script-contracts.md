# 脚本接口与调用契约（按需加载）

> **加载时机**：仅当进入**阶段 3**（错题归档 / 墨墨导入）调用脚本前，按需读取本文件。日常讲题（阶段 0-2）无需加载，保持上下文精简。分支 B（纯词汇/归档类）会话同样适用。
>
> ⚠️ **【强约束】AI 严禁调用 view_file / grep 查看 `scripts/` 目录下的 Python 源码文件。所有脚本调用方式、参数和 JSON Schema 完全以本契约规范为准，直接构造 JSON 与执行命令行。只有脚本报错且 stderr 无法定位时，才允许定点查看报错位置。**
>
> 📁 **输入模式与传参约定（针对 Android / PRoot 环境优化）**：
> - **批量数据优先写临时文件传路径（最推荐）**：由于移动端（Android / Open Minis / PRoot）环境下的 shell 执行工具有单条命令长度上限（约 1000 字符），且禁止使用 heredoc 管道（如 `cat << 'EOF'`），而全篇词汇列表（25~30 词约 2.5KB）与错题归档数组（约 4~7KB）均远超该限制。因此，**词汇批量导入与错题归档默认优先通过 `file_write` 单步写入临时文件并传文件路径调用**。临时文件路径必须使用工作区相对路径（如 `tmp/free-kaoyan-new-type/<本篇唯一ID>/error.json`）或工作区内合法路径（如 `/var/minis/workspace/tmp/free-kaoyan-new-type/<本篇唯一ID>/...`），**严禁直接使用系统根目录 `/tmp/...`（在 Android / Open Minis 等沙箱容器中无法解析，会导致工具调用报错）**。
> - **脚本原生自愈与自动清理（零残留）**：**严禁在 shell 额外调用 `mkdir -p`**（脚本内部已原生支持父目录自动递归创建）。`record_error.py` 与 `memo_import.py` 处理临时文件成功后**默认自动删除输入 JSON 临时文件及其变空的临时父目录**（`--keep-json` 可保留），实现**无残留安全闭环**，完全无需调用方手动清理。
> - **直接参数/管道模式**：命令行直接传参 `--json '<JSON字符串>'` 或 stdin 管道输入仅适用于单条轻量查询（如 `--query` 或极简验证）以及无命令行长度约束的桌面环境。


## 1. 新题型错题本批量归档：`scripts/record_error.py`

- **命令格式**：`python scripts/record_error.py [--file <路径>] [--info] --json <JSON字符串或文件路径> [--keep-json]`（亦支持 stdin 管道输入，**建议省略 `--file` 走默认安全持久化路径**）
- **功能特性**：
  - **安全持久化与路径自愈**：`--file` 参数可选（传入目录或 `.md` 时自动自愈为 `考研英语/新题型错题本.md`）。在 Open Minis / Android PRoot 环境下，默认优先探测已挂载的外部文档目录（`/var/minis/mounts/Documents/考研英语/新题型错题本.md`）或系统公共文档目录（`/storage/emulated/0/Documents/考研英语/新题型错题本.md`），支持 Obsidian、WPS 或自带文件管理器直接查阅；若未挂载则安全保存在 `/var/minis/workspace/新题型错题本.md`；支持环境变量 `KAOYAN_NEW_TYPE_ERROR_NOTEBOOK` 或 `KAOYAN_ERROR_NOTEBOOK` 自定义路径；
  - **存储状态诊断**：支持 `--info` / `--status` 快速查看当前解析到的错题本路径、可写性与已存错题数量；
  - **历史数据自动迁移**：若检测到旧 Skill 目录中残留有效错题，首次归档时自动将历史条目无损合并至持久化错题本，并备份旧文件；
  - **新题型 ID 规范化**：强制标准新题型 ID 命名（`YYYY-新题型-Q[41-45]`，如 `2025-新题型-Q41`，自动纠偏 `PartB`、`B`、`Q41` 等异形命名；亦兼容传统阅读 `YYYY-T[1-4]-Q[21-40]`）；
  - **严密校验与闭环归档**：支持刷次标签（默认 `一刷`）、13 类错误类型与能力短板严格校验、记录选项与副错误类型、YAML frontmatter + 正文格式批量追加（只追加、不覆盖、自动清理占位符 `（暂无）`）。**归档成功后默认自动删除输入 JSON 临时文件及其变空的临时父目录**（`--keep-json` 保留）。
- **输入 JSON Schema**（错题对象数组）：
  ```json
  [
    {
      "id": "2025-新题型-Q41",
      "round": "一刷",
      "question_type": "小标题匹配",
      "error_type": "细节背离主旨",
      "secondary_error_types": ["无对应内容"],
      "user_answer": "C",
      "correct_answer": "A",
      "ability_shortboard": "主旨",
      "keyword": "Champion your idea",
      "location": "Para 1 L3-5",
      "restore": "错误还原 (用户自述思路 + 诊断思维误区)",
      "attribution": "方法论归因 (违背/忽略的原则)",
      "lesson": "教训金句 (一句话前瞻策略)",
      "analysis": "详细复盘正文"
    }
  ]
  ```
- **字段别名兼容**：`round` (支持 `刷次`), `question_type` (支持 `type`/`题型`), `ability_shortboard` (支持 `shortboard`/`ability`/`能力短板`), `error_type` (支持 `error`/`错误类型`), `secondary_error_types` (支持 `secondary_errors`/`副错误类型`), `user_answer` (支持 `user`/`我的答案`), `correct_answer` (支持 `answer`/`正确答案`), `analysis` (支持 `body`/`content`/`正文`)。
- **枚举约束**：
  - `id` 规范格式为 `YYYY-新题型-Q[41-45]`。
  - `round` 默认 `一刷`。
  - `question_type` 默认为 `小标题匹配`（或 `多项对应`）。
  - `error_type` 必须严格属于 13 类之一：`定位错误`、`无对应内容`、`过度推理`、`偷换概念/嫁接`、`因果倒置`、`态度背离`、`细节背离主旨`、`绝对化误选`、`审题不清`、`比较/时态偷换`、`词义误解`、`长难句误读`、`选项误读`。
  - `secondary_error_types` 数组元素必须为 13 类枚举之一（可选）。
  - `ability_shortboard` 必须严格属于：`词汇`、`语法`、`主旨` 之一。

## 2. 核心词汇校验与墨墨背单词一键导入：`scripts/memo_import.py`

- **命令格式**：`python scripts/memo_import.py --json <JSON字符串或文件路径> [--validate-only] [--dry-run] [--query <词条>] [--format text|json|markdown] [--keep-json]`（亦支持 stdin 管道输入）
- **Token 机制**：自动从环境变量 `MAIMEMOTOKEN` 或 `MAIMEMO_TOKEN` 读取，无需显式传 `--token`。
- **功能特性**：
  - **一体化词汇校验**：自动字段归一化、忽略大小写去重、强制 ≤30 个词条截断保护；`--validate-only` 模式仅进行去重校验与截断，不发起网络请求；`--query <词条>` 快速检测单词/词组在墨墨平台的收录状态；
  - **词组精准查询与核心词提取**：词组先通过墨墨 API 全量查询收录状态；若未收录，**绝不粗暴将短语全部单词打散推入生词本**，而是自动提取或使用显式指定的 `keyword` 核心词（跳过停用词与高频泛词）仅导入核心关键词，伴随泛词在报告中明确作为「跳过伴随词」展示；
  - **智能原型还原**：未直接收录的词条自动执行**基于规则的后缀还原（复数 `-s/-es`、过去分词 `-ed`、分词 `-ing`）原型二次查询**；
  - **学习状态自动分流**：比对已有学习记录后自动将新词添加待背、旧词提升提前复习；
  - **单步直接执行**：用户在正文审阅确认后，**直接执行正式导入**。**正式导入成功后默认自动删除输入 JSON 临时文件及其变空的临时父目录**（`--keep-json` 保留；`--dry-run` / `--validate-only` 不删除）。API 类脚本建议设 `timeout ≥ 120s`。
- **输入 JSON Schema**（词汇对象数组）：
  ```json
  [
    {
      "word": "单词或词组 (如 champion your idea)",
      "keyword": "可选：核心关键词 (如 champion；未填写时脚本自动提取)",
      "meaning": "文中释义 (如 捍卫/推销你的想法)",
      "tone": "态度色彩 (如 正面)",
      "source": "出处 (如 Q41 题眼句)"
    }
  ]
  ```
- **输出**：stdout 打印分类统计报告（包含新加待背、提前复习、词组提取核心词明细、跳过虚词/伴随词、无法识别及统计汇总）；成功清理时追加一行 `🧹 已自动清理临时输入文件: <路径>`。
