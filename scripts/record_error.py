#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
record_error.py - 考研英语阅读错题记录脚本
将错题以 YAML frontmatter + 正文格式只追加写入错题本.md
支持单条与批量 (JSON 数组) 追加、ID 自动去重递增与 12 类错误枚举/能力短板校验。
归档成功后默认自动删除输入 JSON 临时文件（--keep-json 可保留），并尝试清理因此变空的临时父目录（最多向上两级，仅删空目录），无需调用方手动清理。
"""

import sys
import os
import re
import json
import argparse

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# 13 类错误类型封闭枚举
ERROR_TYPES = [
    "定位错误",
    "无对应内容",
    "过度推理",
    "偷换概念/嫁接",
    "因果倒置",
    "态度背离",
    "细节背离主旨",
    "绝对化误选",
    "审题不清",
    "比较/时态偷换",
    "词义误解",
    "长难句误读",
    "选项误读"
]

# 能力短板封闭集合
ABILITY_SHORTBOARDS = ["词汇", "语法", "主旨"]

DEFAULT_FILE_NAME = "新题型错题本.md"

def sanitize_notebook_path(raw_path: str) -> str:
    """
    对错题本路径进行安全净化与自愈：
    1. 若因环境编码剥离等原因导致文件名退化为 '.md'，自动纠偏至 '<parent>/考研英语/新题型错题本.md'；
    2. 若路径指向已存在的目录或以斜杠结尾，自动在该目录下追加 '考研英语/新题型错题本.md'（若目录已是 '考研英语' 则追加 '新题型错题本.md'）；
    3. 保证返回绝对路径。
    """
    if not raw_path:
        return ""
    p = os.path.expanduser(raw_path.strip())
    base = os.path.basename(p)
    parent = os.path.dirname(p)

    if base == ".md":
        if os.path.basename(parent) == "考研英语":
            return os.path.abspath(os.path.join(parent, DEFAULT_FILE_NAME))
        return os.path.abspath(os.path.join(parent, "考研英语", DEFAULT_FILE_NAME))

    if os.path.isdir(p) or p.endswith("/") or p.endswith("\\"):
        clean_p = p.rstrip("/\\")
        if os.path.basename(clean_p) == "考研英语":
            return os.path.abspath(os.path.join(clean_p, DEFAULT_FILE_NAME))
        return os.path.abspath(os.path.join(clean_p, "考研英语", DEFAULT_FILE_NAME))

    return os.path.abspath(p)


def get_default_notebook_path() -> str:
    """
    智能解析新题型错题本安全持久化存储路径，彻底与 Skill 目录解耦（保障更新/重装 Skill 绝不丢失）：
    1. 环境变量优先：KAOYAN_NEW_TYPE_ERROR_NOTEBOOK、KAOYAN_ERROR_NOTEBOOK 或 ERROR_NOTEBOOK_PATH（带自动路径自愈）
    2. Open Minis 外部挂载 Documents 优先（/var/minis/mounts/Documents/考研英语/新题型错题本.md）
    3. Android 手机公共文档目录原生探测：
       - /storage/emulated/0/Documents/考研英语/新题型错题本.md
       - /sdcard/Documents/考研英语/新题型错题本.md
       - /storage/emulated/0/Download/考研英语/新题型错题本.md
       - /sdcard/Download/考研英语/新题型错题本.md
    4. Open Minis 沙盒持久工作区降级：
       - /var/minis/workspace/新题型错题本.md
    5. PC / 常规环境（Windows / macOS / Linux）公共文档目录：
       - ~/Documents/考研英语/新题型错题本.md
    6. 全局用户主目录安全兜底：
       - ~/.free-kaoyan-reading/新题型错题本.md
    """
    # 1. 环境变量优先（进行路径自愈净化）
    env_path = os.environ.get("KAOYAN_NEW_TYPE_ERROR_NOTEBOOK") or os.environ.get("KAOYAN_ERROR_NOTEBOOK") or os.environ.get("ERROR_NOTEBOOK_PATH")
    if env_path:
        return sanitize_notebook_path(env_path)

    # 2. Open Minis 外部挂载目录与 Android 沙盒优先
    is_android_or_minis = (os.name != 'nt') or os.path.exists("/var/minis")
    if is_android_or_minis:
        # 优先检测 Open Minis 挂载的外部目录
        minis_mount_candidates = [
            f"/var/minis/mounts/Documents/考研英语/{DEFAULT_FILE_NAME}",
            f"/var/minis/mounts/documents/考研英语/{DEFAULT_FILE_NAME}",
            f"/var/minis/mounts/Documents/{DEFAULT_FILE_NAME}",
            f"/var/minis/mounts/documents/{DEFAULT_FILE_NAME}",
        ]
        if os.path.isdir("/var/minis/mounts"):
            try:
                for entry in os.listdir("/var/minis/mounts"):
                    sub = os.path.join("/var/minis/mounts", entry)
                    if os.path.isdir(sub) and entry.lower() in ["documents", "document", "docs"]:
                        cand = os.path.join(sub, "考研英语", DEFAULT_FILE_NAME)
                        if cand not in minis_mount_candidates:
                            minis_mount_candidates.insert(0, cand)
            except Exception:
                pass

        for cand in minis_mount_candidates:
            try:
                parent = os.path.dirname(cand)
                # 检查挂载根路径是否存在
                mount_root = parent
                while mount_root and not os.path.exists(mount_root) and mount_root != "/":
                    mount_root = os.path.dirname(mount_root)
                if mount_root and mount_root != "/" and os.path.exists(mount_root):
                    os.makedirs(parent, exist_ok=True)
                    test_file = os.path.join(parent, ".perm_test")
                    with open(test_file, "w", encoding="utf-8") as f:
                        f.write("1")
                    os.remove(test_file)
                    return cand
            except Exception:
                continue

        # 3. Android 原生公共存储路径探测
        android_candidates = [
            f"/storage/emulated/0/Documents/考研英语/{DEFAULT_FILE_NAME}",
            f"/sdcard/Documents/考研英语/{DEFAULT_FILE_NAME}",
            f"/storage/emulated/0/Download/考研英语/{DEFAULT_FILE_NAME}",
            f"/sdcard/Download/考研英语/{DEFAULT_FILE_NAME}",
        ]
        for cand in android_candidates:
            try:
                parent = os.path.dirname(cand)
                if os.path.exists(parent) or os.path.exists("/storage/emulated/0") or os.path.exists("/sdcard"):
                    os.makedirs(parent, exist_ok=True)
                    test_file = os.path.join(parent, ".perm_test")
                    with open(test_file, "w", encoding="utf-8") as f:
                        f.write("1")
                    os.remove(test_file)
                    return cand
            except Exception:
                continue

        # 4. Open Minis 官方 workspace 降级
        minis_ws = "/var/minis/workspace"
        if os.path.exists(minis_ws):
            try:
                os.makedirs(minis_ws, exist_ok=True)
                cand = os.path.join(minis_ws, DEFAULT_FILE_NAME)
                test_file = os.path.join(minis_ws, ".perm_test")
                with open(test_file, "w", encoding="utf-8") as f:
                    f.write("1")
                os.remove(test_file)
                return cand
            except Exception:
                pass

    # 5. PC / 常规系统（Windows / macOS / Linux）用户文档目录
    try:
        home = os.path.expanduser("~")
        docs_dir = os.path.join(home, "Documents", "考研英语")
        if os.path.exists(os.path.join(home, "Documents")):
            os.makedirs(docs_dir, exist_ok=True)
            return os.path.join(docs_dir, DEFAULT_FILE_NAME)
    except Exception:
        pass

    # 6. 全局用户主目录安全兜底
    try:
        home_fallback = os.path.join(os.path.expanduser("~"), ".free-kaoyan-reading")
        os.makedirs(home_fallback, exist_ok=True)
        return os.path.join(home_fallback, DEFAULT_FILE_NAME)
    except Exception:
        return os.path.abspath(DEFAULT_FILE_NAME)


def migrate_legacy_notebook_if_needed(target_path: str):
    """
    检查旧 Skill 目录下是否存在历史遗留错题数据。
    若存在且目标文件与旧文件不同，将旧文件中的有效条目无损合并至新目标持久化路径，并备份旧文件。
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    skill_dir = os.path.dirname(script_dir)
    legacy_candidates = [
        os.path.join(skill_dir, DEFAULT_FILE_NAME),
        os.path.join(os.path.dirname(skill_dir), "free", DEFAULT_FILE_NAME),
        os.path.abspath(DEFAULT_FILE_NAME),
    ]
    target_abs = os.path.abspath(target_path)

    for leg_path in legacy_candidates:
        leg_abs = os.path.abspath(leg_path)
        if leg_abs == target_abs or not os.path.isfile(leg_abs):
            continue

        try:
            with open(leg_abs, "r", encoding="utf-8") as f:
                legacy_text = f.read()

            # 检查旧文件中是否包含真实的错题条目（特征: id: XXX）
            if not re.search(r"^id:\s*.+$", legacy_text, re.MULTILINE):
                continue

            target_ids = get_existing_ids(target_abs)
            if not os.path.exists(target_abs) or os.path.getsize(target_abs) == 0:
                parent_dir = os.path.dirname(target_abs)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)
                with open(target_abs, "w", encoding="utf-8") as f:
                    f.write(legacy_text)
                print(f"[MIGRATION] 已将旧错题本完整迁移至持久化路径: {target_abs}")
            else:
                raw_entries = re.split(r"(?=\n---\nid:)", "\n" + legacy_text)
                to_append = []
                for entry in raw_entries:
                    m = re.search(r"^id:\s*(.+)$", entry.strip(), re.MULTILINE)
                    if m and m.group(1).strip() not in target_ids:
                        to_append.append(entry.strip() + "\n\n")
                if to_append:
                    with open(target_abs, "a", encoding="utf-8") as f:
                        f.write("\n" + "".join(to_append))
                    print(f"[MIGRATION] 已将旧错题本中的历史条目合并至持久化路径: {target_abs}")

            bak_path = leg_abs + ".migrated.bak"
            if os.path.exists(bak_path):
                os.remove(bak_path)
            os.rename(leg_abs, bak_path)
        except Exception as e:
            print(f"[WARNING] 旧错题迁移提示 (跳过): {e}", file=sys.stderr)


def get_existing_ids(file_path):
    if not os.path.exists(file_path):
        return set()
    ids = set()
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^id:\s*(.+)$", line.strip())
            if m:
                ids.add(m.group(1).strip())
    return ids

def normalize_standard_id(raw_id: str) -> str:
    """
    规范标准 ID 命名规则:
    - 新题型: YYYY-新题型-Q[41-45]
    - 传统阅读: YYYY-T[1-4]-Q[21-40]
    自动纠偏 '2025-新题型-Q41', '2025-PartB-Q41', '2025-B-41', '2025-Q41', '2015-英二T4-Q38' 等格式
    """
    if not raw_id:
        return "UNKNOWN-新题型-Q0"
    raw_id = raw_id.strip()
    # 优先匹配显式标注新题型或 PartB 的 Q41-Q45
    m_new = re.search(r"(\d{4}).*?(新题型|Part\s*B|PartB|B).*?Q?([4][1-5])", raw_id, re.IGNORECASE)
    if m_new:
        return f"{m_new.group(1)}-新题型-Q{m_new.group(3)}"
    # 匹配仅包含年份和 Q41-Q45 的新题型题号 (如 2025-Q41)
    m_new_simple = re.search(r"(\d{4}).*?Q?([4][1-5])\b", raw_id, re.IGNORECASE)
    if m_new_simple:
        return f"{m_new_simple.group(1)}-新题型-Q{m_new_simple.group(2)}"
    # 兼容传统阅读标准命名 YYYY-T[1-4]-Q[21-40]
    m_trad = re.search(r"(\d{4}).*?T?([1-4]).*?Q?([2-4]\d)", raw_id, re.IGNORECASE)
    if m_trad:
        return f"{m_trad.group(1)}-T{m_trad.group(2)}-Q{m_trad.group(3)}"
    return raw_id

def generate_unique_id(base_id, existing_ids):
    if base_id not in existing_ids:
        return base_id
    index = 2
    while f"{base_id}-{index}" in existing_ids:
        index += 1
    return f"{base_id}-{index}"

def _get_field(data, keys, default=""):
    for k in keys:
        if k in data and data[k] is not None:
            val = str(data[k]).strip()
            if val:
                return val
    return default

def format_error_entry(data, existing_ids):
    # 校验错误类型（支持多种常见别名）
    error_type = _get_field(data, ["error_type", "error", "type_of_error", "错误类型"])
    if error_type not in ERROR_TYPES:
        print(f"[ERROR] 非法错误类型: '{error_type}'。必须严格属于 13 类封闭枚举之一：\n{', '.join(ERROR_TYPES)}", file=sys.stderr)
        sys.exit(1)

    # 校验能力短板（支持多种常见别名）
    shortboard = _get_field(data, ["ability_shortboard", "shortboard", "ability", "能力短板"])
    if shortboard not in ABILITY_SHORTBOARDS:
        print(f"[ERROR] 非法能力短板: '{shortboard}'。必须属于 {ABILITY_SHORTBOARDS} 之一", file=sys.stderr)
        sys.exit(1)

    base_id = _get_field(data, ["id", "ID", "item_id"], "UNKNOWN-新题型-Q0")
    base_id = normalize_standard_id(base_id)
    entry_id = generate_unique_id(base_id, existing_ids)
    existing_ids.add(entry_id)

    # 刷次标签（默认“一刷”）
    round_val = _get_field(data, ["round", "刷次", "review_round", "round_tag"], "一刷")

    # 用户选项与正确选项（可选）
    user_answer = _get_field(data, ["user_answer", "user_choice", "my_answer", "用户选项"])
    correct_answer = _get_field(data, ["correct_answer", "answer", "standard_answer", "正确答案"])

    # secondary_error_types（副错误类型列表，可选）
    sec_errs_raw = data.get("secondary_error_types") or data.get("secondary_errors") or data.get("副错误类型") or []
    if isinstance(sec_errs_raw, str):
        sec_errs_raw = [s.strip() for s in re.split(r"[,，、]", sec_errs_raw) if s.strip()]
    valid_sec_errors = []
    if isinstance(sec_errs_raw, list):
        for se in sec_errs_raw:
            se_str = str(se).strip()
            if se_str in ERROR_TYPES and se_str != error_type and se_str not in valid_sec_errors:
                valid_sec_errors.append(se_str)

    q_type = _get_field(data, ["question_type", "type", "q_type", "题型"], "小标题匹配")
    keyword = _get_field(data, ["keyword", "keywords", "key", "解题关键词"])
    location = _get_field(data, ["location", "loc", "原文定位"])
    restore = _get_field(data, ["restore", "thought", "错误还原"])
    attribution = _get_field(data, ["attribution", "attr", "方法论归因"])
    lesson = _get_field(data, ["lesson", "takeaway", "教训金句"])
    analysis = _get_field(data, ["analysis", "body", "content", "正文"])
    if not analysis:
        analysis = f"错误还原：{restore}\n方法论归因：{attribution}\n教训金句：{lesson}"

    frontmatter_lines = [
        "---",
        f"id: {entry_id}",
        f"刷次: {round_val}",
        f"题型: {q_type}",
        f"error_type: {error_type}",
    ]
    if valid_sec_errors:
        frontmatter_lines.append(f"secondary_error_types: [{', '.join(valid_sec_errors)}]")
    if user_answer:
        frontmatter_lines.append(f"user_answer: {user_answer}")
    if correct_answer:
        frontmatter_lines.append(f"correct_answer: {correct_answer}")
    frontmatter_lines.extend([
        f"能力短板: {shortboard}",
        f"解题关键词: {keyword}",
        f"原文定位: {location}",
        f"错误还原: {restore}",
        f"方法论归因: {attribution}",
        f"教训金句: {lesson}",
        "---"
    ])
    fm_block = "\n".join(frontmatter_lines)

    entry_content = f"""
{fm_block}

### {entry_id}

{analysis}
"""
    return entry_id, entry_content

def append_errors(file_path, items):
    if not items:
        print("[WARNING] 没有待追加的错题条目。", file=sys.stderr)
        return []

    # 首次归档前，若存在旧 Skill 目录遗留数据，自动安全合并迁移
    migrate_legacy_notebook_if_needed(file_path)

    existing_ids = get_existing_ids(file_path)
    added_ids = []
    contents = []

    for item in items:
        if not isinstance(item, dict):
            continue
        entry_id, content = format_error_entry(item, existing_ids)
        added_ids.append(entry_id)
        contents.append(content)

    if not added_ids:
        print("[ERROR] 未解析到有效错题数据。", file=sys.stderr)
        sys.exit(1)

    parent_dir = os.path.dirname(os.path.abspath(file_path))
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)

    if not os.path.exists(file_path):
        header = "# 新题型错题本\n\n本文件存放考研英语新题型错题，由 FREE考研英语新题型 (free-kaoyan-reading-new-type) 维护。\n\n## 错题列表\n"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(header)
    else:
        # 若存在初始模板占位符“（暂无）”，自动清理剔除
        with open(file_path, "r", encoding="utf-8") as f:
            content_str = f.read()
        if "（暂无）" in content_str or "(暂无)" in content_str:
            cleaned_str = re.sub(r"[（(]暂无[）)]\s*", "", content_str)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(cleaned_str)

    with open(file_path, "a", encoding="utf-8") as f:
        f.write("".join(contents))

    target_abs = os.path.abspath(file_path)
    if len(added_ids) == 1:
        print(f"[SUCCESS] 错题已成功追加至错题本，条目 ID: {added_ids[0]} (存储路径: {target_abs})")
    else:
        print(f"[SUCCESS] 成功批量追加 {len(added_ids)} 条错题至错题本: {', '.join(added_ids)} (存储路径: {target_abs})")

    return added_ids

def cleanup_input_file(path: str) -> bool:
    """
    归档成功后清理输入 JSON 临时文件：删除文件本身，并尝试删除因此变空的临时父目录
    （最多向上两级，仅当目录为空时删除；绝不触碰当前工作目录及更上层）。
    清理失败静默忽略，不影响归档结果。返回是否删除了文件本身。
    """
    try:
        p = os.path.abspath(path)
        if not os.path.isfile(p):
            return False
        os.remove(p)
        parent = os.path.dirname(p)
        cwd = os.path.abspath(os.getcwd())
        for _ in range(2):
            if parent == cwd or os.path.dirname(parent) == parent:
                break
            try:
                os.rmdir(parent)
            except OSError:
                break
            parent = os.path.dirname(parent)
        return True
    except OSError:
        return False


def print_notebook_info(file_path: str = None):
    """打印错题本存储诊断信息"""
    target = sanitize_notebook_path(file_path) if file_path else get_default_notebook_path()
    exists = os.path.isfile(target)
    entry_count = len(get_existing_ids(target)) if exists else 0

    writable = False
    parent = os.path.dirname(target)
    try:
        if parent:
            os.makedirs(parent, exist_ok=True)
        test_file = os.path.join(parent, ".perm_test") if parent else ".perm_test"
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("1")
        os.remove(test_file)
        writable = True
    except Exception:
        writable = False

    print("=== 错题本存储诊断信息 ===")
    print(f"目标路径: {target}")
    print(f"文件存在: {'是' if exists else '否'}")
    print(f"可写状态: {'可写入' if writable else '只读/不可写'}")
    print(f"已存错题: {entry_count} 条")
    if exists and entry_count > 0:
        ids = sorted(list(get_existing_ids(target)))
        print(f"条目清单: {', '.join(ids)}")


def main():
    parser = argparse.ArgumentParser(description="考研英语新题型错题批量/单条追加记录工具")
    parser.add_argument("--file", default=None, help="错题本文件路径（默认: 自动安全持久化，Android 优先写入手机公共 Documents 目录）")
    parser.add_argument("--json", dest="json_input", help="以 JSON 字符串或 JSON 文件路径传入完整参数（支持单个对象或对象数组）")
    parser.add_argument("--info", "--status", action="store_true", help="打印当前错题本存储路径与状态诊断信息")
    parser.add_argument("--id", help="题目唯一 ID (如 2025-新题型-Q41)")
    parser.add_argument("--type", "--question-type", dest="question_type", default="小标题匹配", help="题型 (小标题匹配/多项对应等)")
    parser.add_argument("--error-type", dest="error_type", help="13 类错误类型之一")
    parser.add_argument("--shortboard", "--ability-shortboard", dest="ability_shortboard", help="能力短板 (词汇/语法/主旨)")
    parser.add_argument("--keyword", default="", help="解题关键词")
    parser.add_argument("--location", default="", help="原文定位")
    parser.add_argument("--restore", default="", help="错误还原思路")
    parser.add_argument("--attribution", default="", help="方法论归因")
    parser.add_argument("--lesson", default="", help="教训金句")
    parser.add_argument("--analysis", default="", help="详细复盘正文")
    parser.add_argument("--keep-json", dest="keep_json", action="store_true", help="归档成功后保留输入 JSON 文件（默认自动删除临时输入文件及其变空的父目录）")

    args = parser.parse_args()

    if args.info:
        print_notebook_info(args.file)
        return

    items = []
    if args.json_input:
        if os.path.isfile(args.json_input):
            with open(args.json_input, "r", encoding="utf-8-sig") as f:
                raw_data = json.load(f)
        else:
            raw_data = json.loads(args.json_input)

        if isinstance(raw_data, list):
            items = raw_data
        elif isinstance(raw_data, dict):
            if "errors" in raw_data and isinstance(raw_data["errors"], list):
                items = raw_data["errors"]
            elif "questions" in raw_data and isinstance(raw_data["questions"], list):
                items = raw_data["questions"]
            else:
                items = [raw_data]
    else:
        # Check stdin
        if not sys.stdin.isatty():
            content = sys.stdin.read().strip()
            if content:
                raw_data = json.loads(content)
                if isinstance(raw_data, list):
                    items = raw_data
                elif isinstance(raw_data, dict):
                    if "errors" in raw_data and isinstance(raw_data["errors"], list):
                        items = raw_data["errors"]
                    else:
                        items = [raw_data]
        if not items:
            if not args.error_type or not args.ability_shortboard or not args.id:
                parser.error("必须提供 --id, --error-type, --shortboard 或通过 --json / stdin 提供完整参数（或传 --info 查看状态）")
            items = [{
                "id": args.id,
                "question_type": args.question_type,
                "error_type": args.error_type,
                "ability_shortboard": args.ability_shortboard,
                "keyword": args.keyword,
                "location": args.location,
                "restore": args.restore,
                "attribution": args.attribution,
                "lesson": args.lesson,
                "analysis": args.analysis
            }]

    target_file = sanitize_notebook_path(args.file) if args.file else get_default_notebook_path()
    append_errors(target_file, items)

    # 归档成功后自动清理临时输入文件（--keep-json 例外）
    if (not args.keep_json and args.json_input
            and os.path.isfile(args.json_input)):
        if cleanup_input_file(args.json_input):
            print(f"[CLEANUP] 已自动清理临时输入文件: {os.path.abspath(args.json_input)}")

if __name__ == "__main__":
    main()
