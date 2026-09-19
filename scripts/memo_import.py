#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memo_import.py - 考研英语核心词汇墨墨背单词自动化导入脚本

功能说明：
1. 接收与 vocab_export.py 兼容的词汇清单 JSON（支持文件、字符串与标准输入）。
2. 批量解析词条的 voc_id，词组优先按原形整体查询。
3. 未在墨墨直接收录的词组自动拆分为单个独立单词，二次兜底查询解析。
4. 查询学习记录，自动状态分流：
   - 不在学习计划 -> 调用 /study/add_words 添加至待背队列 (advance=false)；
   - 已在学习计划 -> 调用 /study/advance_study 设置为提前复习。
5. 词组拆分时自动过滤英语虚词（the/of/to 等停用词），避免虚词污染学习队列，报告中单列跳过明细。
6. 结束后输出分类报告（新加待背 / 提前复习 / 词组拆出的单词 / 跳过虚词 / 无法识别）。
7. 支持 --dry-run 预览模式；Token 缺失或接口异常清晰报错退出，不静默失败。
8. 正式导入成功后默认自动删除输入 JSON 临时文件（--keep-json 可保留），并尝试清理因此变空的临时父目录（最多向上两级，仅删空目录），无需调用方手动清理。
"""

import sys
import os
import json
import argparse
import re
import urllib.request
import urllib.error

# Windows 控制台 UTF-8 编码重配置
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE_URL = "https://open.maimemo.com/open/api/v1"
TIMEOUT_SECONDS = 15

# 词组拆分兜底时跳过的英语虚词（冠词/介词/连词/代词/系动词/助动词等）。
# 这些词不构成词汇学习价值，禁止进入墨墨查询与导入，避免污染学习队列。
PHRASE_STOPWORDS = {
    # 冠词
    "a", "an", "the",
    # 连词
    "and", "or", "but", "nor", "so", "yet",
    # 介词
    "of", "to", "in", "on", "at", "by", "for", "with", "from", "as",
    "into", "onto", "upon", "about", "over", "under", "up", "down",
    "out", "off", "than", "that",
    # 代词（含所有格）
    "it", "its", "this", "these", "those", "one's",
    "i", "me", "my", "we", "our", "you", "your",
    "he", "him", "his", "she", "her", "they", "them", "their",
    # 系动词 / 助动词
    "is", "are", "was", "were", "be", "been", "being", "am",
    "do", "does", "did", "has", "had", "have",
}


class MaiMemoAPIError(Exception):
    """墨墨 API 调用异常"""
    pass


def make_api_request(endpoint: str, data: dict = None, token: str = "") -> dict:
    """
    发送 API 请求并返回解析后的 JSON 响应
    """
    url = f"{BASE_URL}{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "free-kaoyan-reading-importer/1.0"
    }

    req_data = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=req_data, headers=headers, method="POST" if data is not None else "GET")

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
            try:
                res_json = json.loads(body)
            except Exception as e:
                raise MaiMemoAPIError(f"API 响应 JSON 解析失败: {e} (原始内容: {body[:200]})")

            if not res_json.get("success", False):
                errors = res_json.get("errors", [])
                err_msgs = [f"[{err.get('code', 'UNKNOWN')}] {err.get('msg', '')}" for err in errors]
                err_str = "; ".join(err_msgs) if err_msgs else "未知业务错误"
                raise MaiMemoAPIError(f"API 返回错误: {err_str}")

            return res_json

    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
            err_json = json.loads(err_body)
            errors = err_json.get("errors", [])
            err_msgs = [f"[{err.get('code', 'HTTP_' + str(e.code))}] {err.get('msg', '')}" for err in errors]
            err_str = "; ".join(err_msgs) if err_msgs else err_body
        except Exception:
            err_str = err_body if err_body else str(e.reason)
        raise MaiMemoAPIError(f"HTTP {e.code} 请求失败 ({endpoint}): {err_str}")
    except urllib.error.URLError as e:
        raise MaiMemoAPIError(f"网络连接失败 ({endpoint}): {e.reason}")
    except TimeoutError:
        raise MaiMemoAPIError(f"请求超时 ({endpoint}, >{TIMEOUT_SECONDS}s)")


MAX_VOCAB_LIMIT = 30


def normalize_vocab_entries(raw_input) -> tuple:
    """
    解析并归一化词汇清单列表。
    支持纯字符串列表、字典列表，自动去重（忽略大小写，保留首次出现），
    自动剔除缺 word 字段的无效项，强制限制 <= MAX_VOCAB_LIMIT。
    返回 (deduped_dicts, deduped_terms, duplicates, truncated, invalid)
    """
    if isinstance(raw_input, dict):
        if "words" in raw_input and isinstance(raw_input["words"], list):
            raw_input = raw_input["words"]
        elif "vocab" in raw_input and isinstance(raw_input["vocab"], list):
            raw_input = raw_input["vocab"]
        else:
            raw_input = [raw_input]

    if not isinstance(raw_input, list):
        raise ValueError("输入数据必须为词汇列表或包含 words/vocab 列表的对象")

    seen = set()
    deduped_dicts = []
    deduped_terms = []
    duplicates = []
    invalid = []

    for item in raw_input:
        if isinstance(item, str):
            term = item.strip()
            dict_item = {"word": term, "meaning": "", "tone": "", "source": ""}
        elif isinstance(item, dict):
            raw_word = item.get("word", item.get("term", item.get("单词", item.get("词组", ""))))
            term = raw_word.strip() if isinstance(raw_word, str) else ""
            raw_kw = item.get("keyword", item.get("core_keyword", item.get("关键词", "")))
            kw_str = raw_kw.strip() if isinstance(raw_kw, str) else ""
            dict_item = {
                "word": term,
                "keyword": kw_str,
                "meaning": (item.get("meaning", item.get("definition", item.get("释义", item.get("文中释义", "")))) or "").strip(),
                "tone": (item.get("tone", item.get("attitude", item.get("态度", item.get("态度色彩", "")))) or "").strip(),
                "source": (item.get("source", item.get("origin", item.get("出处", ""))) or "").strip(),
            }
        else:
            term = ""
            dict_item = None

        if not term:
            if item:
                invalid.append(item)
            continue

        key = term.lower()
        if key in seen:
            duplicates.append(term)
            continue
        seen.add(key)
        deduped_dicts.append(dict_item)
        deduped_terms.append(term)

    truncated = []
    if len(deduped_dicts) > MAX_VOCAB_LIMIT:
        truncated = deduped_dicts[MAX_VOCAB_LIMIT:]
        deduped_dicts = deduped_dicts[:MAX_VOCAB_LIMIT]
        deduped_terms = deduped_terms[:MAX_VOCAB_LIMIT]

    return deduped_dicts, deduped_terms, duplicates, truncated, invalid


def parse_input_vocab(raw_input) -> tuple:
    """
    解析并提取词汇清单列表（去重并截断至 MAX_VOCAB_LIMIT）
    返回: (terms, term_keywords_map)
    """
    deduped_dicts, terms, _, truncated, _ = normalize_vocab_entries(raw_input)
    if truncated:
        print(f"[WARNING] 词汇数量超过最大限制 ({MAX_VOCAB_LIMIT})，已自动截断保留前 {MAX_VOCAB_LIMIT} 个词条。", file=sys.stderr)
    term_keywords = {}
    for d in deduped_dicts:
        if d.get("keyword"):
            term_keywords[d["word"].lower()] = d["keyword"].strip()
    return terms, term_keywords


def format_validation_report(deduped_dicts, duplicates, truncated, invalid, output_format="check") -> str:
    """
    格式化词汇校验报告（用于 --validate-only）
    """
    total_count = len(deduped_dicts)
    if output_format == "json":
        return json.dumps(deduped_dicts, ensure_ascii=False, indent=2)

    if output_format == "markdown":
        lines = [f"### 本篇核心单词与词组清单（共 {total_count} 词，已去重且 ≤{MAX_VOCAB_LIMIT}）\n"]
        lines.append("| # | 单词 / 词组 | 文中释义 | 态度色彩 | 出处 |")
        lines.append("| :--- | :--- | :--- | :--- | :--- |")
        for idx, item in enumerate(deduped_dicts, 1):
            word = item['word'].replace('|', '\\|').replace('\n', ' ')
            meaning = item['meaning'].replace('|', '\\|').replace('\n', ' ') if item['meaning'] else "-"
            tone = item['tone'].replace('|', '\\|').replace('\n', ' ') if item['tone'] else "-"
            source = item['source'].replace('|', '\\|').replace('\n', ' ') if item['source'] else "-"
            lines.append(f"| {idx} | {word} | {meaning} | {tone} | {source} |")
        return "\n".join(lines)

    # 默认 check 简报
    if total_count == 0:
        return "❌ 校验失败：清单为空（全部条目无效或无词条）。"

    has_corrections = bool(duplicates or truncated or invalid)
    lines = []
    if has_corrections:
        lines.append(f"⚠️ 校验修正：最终 {total_count} 词（去重 {len(duplicates)} | 截断 {len(truncated)} | 无效条目 {len(invalid)}）")
        if duplicates:
            lines.append(f"  移除重复: {', '.join(duplicates)}")
        if truncated:
            lines.append(f"  截断移除: {', '.join(t['word'] for t in truncated)}")
        if invalid:
            lines.append(f"  无效条目（缺 word 字段）: {len(invalid)} 个")
        lines.append(f"\n最终清单（共 {total_count} 词，请严格按此清单与词序在正文渲染 Markdown 表格）:")
        lines.append(", ".join(item['word'] for item in deduped_dicts))
    else:
        lines.append(f"✅ 校验通过：共 {total_count} 词，无重复、未超限、无无效条目。请在正文中按原清单渲染 Markdown 表格。")
    return "\n".join(lines)



def split_phrase_into_words(phrase: str) -> tuple:
    """
    将未直接收录的短语拆解为单个单词。
    过滤标点符号与虚词（PHRASE_STOPWORDS），返回 (实义词列表, 跳过的虚词列表)。
    """
    words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", phrase)
    cleaned = []
    skipped = []
    seen = set()
    for w in words:
        w_clean = w.strip()
        if not w_clean:
            continue
        w_key = w_clean.lower()
        if w_key in seen:
            continue
        seen.add(w_key)
        if w_key in PHRASE_STOPWORDS:
            skipped.append(w_clean)
        else:
            cleaned.append(w_clean)
    return cleaned, skipped


def extract_core_keyword(phrase: str, explicit_keyword: str = "") -> tuple:
    """
    从短语中提取核心关键词。
    返回: (核心关键词, 跳过的伴随词列表, 跳过的虚词列表)
    提取规则:
    1. 若提供了 explicit_keyword，直接以其为核心关键词；
    2. 否则进行分词并剔除 PHRASE_STOPWORDS 虚词；
    3. 若剩余 1 个词，则为核心关键词；
    4. 若剩余多个词：
       - 优先排除泛指代词/名词弱实词（people, person, man, men, woman, women, thing, things, time, day, way）；
       - 其次排除通用程度/泛化形容词（big, small, good, bad, great, high, low, general）；
       - 剩余词通常即为题眼核心实义词（如 regular people -> regular；big jump -> jump；general direction -> direction）；
       - 若仍有多个词，默认取最后一个实义词（中心词，如 office speak -> speak）。
    """
    words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", phrase)
    if explicit_keyword and explicit_keyword.strip():
        kw = explicit_keyword.strip()
        skipped_stopwords = [w for w in words if w.lower() in PHRASE_STOPWORDS]
        skipped_companions = [w for w in words if w.lower() not in PHRASE_STOPWORDS and w.lower() != kw.lower()]
        return kw, skipped_companions, skipped_stopwords

    cleaned = []
    skipped_stopwords = []
    seen = set()
    for w in words:
        w_clean = w.strip()
        if not w_clean:
            continue
        w_key = w_clean.lower()
        if w_key in seen:
            continue
        seen.add(w_key)
        if w_key in PHRASE_STOPWORDS:
            skipped_stopwords.append(w_clean)
        else:
            cleaned.append(w_clean)

    if not cleaned:
        return "", [], skipped_stopwords

    if len(cleaned) == 1:
        return cleaned[0], [], skipped_stopwords

    SUPER_GENERIC_NOUNS = {"people", "person", "man", "men", "woman", "women", "thing", "things", "time", "day", "way"}
    SUPER_GENERIC_ADJS = {"big", "small", "good", "bad", "great", "high", "low", "general"}

    # 1. 排除泛指代词/名词
    non_generic_nouns = [w for w in cleaned if w.lower() not in SUPER_GENERIC_NOUNS]
    if len(non_generic_nouns) == 1:
        chosen = non_generic_nouns[0]
        companions = [w for w in cleaned if w.lower() != chosen.lower()]
        return chosen, companions, skipped_stopwords

    # 2. 进一步排除纯程度形容词
    candidates = non_generic_nouns if non_generic_nouns else cleaned
    non_generic_adjs = [w for w in candidates if w.lower() not in SUPER_GENERIC_ADJS]
    if len(non_generic_adjs) == 1:
        chosen = non_generic_adjs[0]
        companions = [w for w in cleaned if w.lower() != chosen.lower()]
        return chosen, companions, skipped_stopwords

    if non_generic_adjs:
        chosen = non_generic_adjs[-1]
    else:
        chosen = cleaned[-1]

    companions = [w for w in cleaned if w.lower() != chosen.lower()]
    return chosen, companions, skipped_stopwords


def get_lemma_candidates(word: str) -> list:
    """
    针对直接查询未命中的词条，生成基于规则的后缀还原候选（复数 -s/-es、过去分词 -ed、分词 -ing）。
    返回按置信度排序的原型候选词列表（全小写，不包含自身）。
    """
    w = word.strip().lower()
    if len(w) <= 3:
        return []

    candidates = []

    def add(c):
        if c and c != w and len(c) >= 2 and c not in candidates:
            candidates.append(c)

    # 1. 过去式 / 过去分词 -ed
    if w.endswith("ied") and len(w) > 4:
        add(w[:-3] + "y")  # studied -> study, denied -> deny
    elif w.endswith("ed") and len(w) > 3:
        # 双写辅音 + ed (dimmed -> dim, stopped -> stop, planned -> plan)
        if len(w) > 4 and w[-3] == w[-4] and w[-3] in "bdfgklmnprstz":
            add(w[:-3])
        # 去掉 d (graduated -> graduate, improved -> improve, divided -> divide)
        add(w[:-1])
        # 去掉 ed (suggested -> suggest, affected -> affect)
        add(w[:-2])

    # 2. 现在分词 / 动名词 -ing
    if w.endswith("ing") and len(w) > 4:
        # 双写辅音 + ing (beginning -> begin, running -> run)
        if len(w) > 5 and w[-4] == w[-5] and w[-4] in "bdfgklmnprstz":
            add(w[:-4])
        # 去掉 ing (spending -> spend, affecting -> affect)
        add(w[:-3])
        # ing -> e (reshaping -> reshape, stagnating -> stagnate, deciding -> decide)
        add(w[:-3] + "e")

    # 3. 复数 / 第三人称单数 -s / -es
    if w.endswith("ies") and len(w) > 4:
        add(w[:-3] + "y")  # categories -> category
    elif w.endswith("es") and len(w) > 3:
        # 去掉 s 保留 e (divides -> divide, chances -> chance, houses -> house)
        add(w[:-1])
        # 去掉 es (classes -> class, watches -> watch, boxes -> box)
        add(w[:-2])
    elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        add(w[:-1])  # recessions -> recession, times -> time

    return candidates


def resolve_vocabularies(terms: list, token: str, term_keywords: dict = None) -> tuple:
    """
    批量查询词汇 ID，优先查询整词/词组，未收录词组自动提取核心关键词二次查询
    返回:
      direct_matches: {term: voc_id}
      phrase_splits: {phrase: [keyword]}
      split_matches: {keyword: voc_id}
      unrecognized: [unmatched_single_words or failed_keywords]
      phrase_stopwords: {phrase: [skipped_stopword1, ...]} 拆分时跳过的虚词
      phrase_companions: {phrase: [skipped_companion1, ...]} 拆分时跳过的伴随词
    """
    direct_matches = {}
    phrase_splits = {}
    split_matches = {}
    unrecognized = []
    phrase_stopwords = {}
    phrase_companions = {}
    term_keywords = term_keywords or {}

    if not terms:
        return direct_matches, phrase_splits, split_matches, unrecognized, phrase_stopwords, phrase_companions

    # 1. 批量查询第一批（所有原形词条，包含整句短语）
    # 同时提交原形和全小写以提升命中率
    spelling_query_set = set()
    for t in terms:
        spelling_query_set.add(t)
        if t.lower() != t:
            spelling_query_set.add(t.lower())

    res = make_api_request("/vocabulary/query", {"spellings": list(spelling_query_set)}, token=token)
    voc_list = res.get("data", {}).get("voc", [])
    
    # 建立 spelling.lower() -> voc_id 映射
    lookup_map = {}
    for item in voc_list:
        lookup_map[item["spelling"].lower()] = (item["id"], item["spelling"])

    unmatched_terms = []
    for t in terms:
        t_key = t.lower()
        if t_key in lookup_map:
            direct_matches[t] = lookup_map[t_key][0]
        else:
            unmatched_terms.append(t)

    # 2. 对未命中的词条进行分类：是短语则提取核心关键词，是单字则暂归入无法识别
    words_to_query = set()
    for t in unmatched_terms:
        raw_token_count = len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", t))
        if raw_token_count > 1:
            explicit_kw = term_keywords.get(t.lower(), "")
            kw, companions, skipped = extract_core_keyword(t, explicit_keyword=explicit_kw)
            if skipped:
                phrase_stopwords[t] = skipped
            if companions:
                phrase_companions[t] = companions
            if not kw:
                # 词组全部由虚词构成，无核心实义词可查，整条跳过
                continue
            phrase_splits[t] = [kw]
            kw_key = kw.lower()
            if kw_key in lookup_map:
                split_matches[kw] = lookup_map[kw_key][0]
            else:
                words_to_query.add(kw)
                if kw.lower() != kw:
                    words_to_query.add(kw.lower())
        else:
            unrecognized.append(t)

    # 3. 针对未在之前查询中命中的核心关键词进行二次查询
    if words_to_query:
        res_split = make_api_request("/vocabulary/query", {"spellings": list(words_to_query)}, token=token)
        split_voc_list = res_split.get("data", {}).get("voc", [])
        for item in split_voc_list:
            lookup_map[item["spelling"].lower()] = (item["id"], item["spelling"])

        # 检查核心关键词是否全部解析
        for phrase, words in phrase_splits.items():
            for w in words:
                w_key = w.lower()
                if w_key in lookup_map:
                    split_matches[w] = lookup_map[w_key][0]
                else:
                    if w not in unrecognized:
                        unrecognized.append(w)

    # 4. 基于规则的后缀还原（复数 -s/-es、过去分词 -ed、分词 -ing）：直接查询失败时自动尝试原型查询
    candidates_to_query = set()
    word_to_candidates = {}
    for u in list(unrecognized):
        if re.fullmatch(r"[A-Za-z]+(?:'[A-Za-z]+)?", u):
            cands = get_lemma_candidates(u)
            if cands:
                word_to_candidates[u] = cands
                for c in cands:
                    c_key = c.lower()
                    if c_key not in lookup_map:
                        candidates_to_query.add(c)

    if candidates_to_query:
        res_lemma = make_api_request("/vocabulary/query", {"spellings": list(candidates_to_query)}, token=token)
        lemma_voc_list = res_lemma.get("data", {}).get("voc", [])
        for item in lemma_voc_list:
            lookup_map[item["spelling"].lower()] = (item["id"], item["spelling"])

    # 检查后缀还原候选是否命中
    for u, cands in word_to_candidates.items():
        for c in cands:
            c_key = c.lower()
            if c_key in lookup_map:
                vid = lookup_map[c_key][0]
                lookup_map[u.lower()] = (vid, lookup_map[c_key][1])
                if u in unrecognized:
                    unrecognized.remove(u)
                if u in terms:
                    direct_matches[u] = vid
                for p, p_words in phrase_splits.items():
                    if u in p_words:
                        split_matches[u] = vid
                break

    return direct_matches, phrase_splits, split_matches, unrecognized, phrase_stopwords, phrase_companions



def partition_and_import(direct_matches: dict, phrase_splits: dict, split_matches: dict, token: str, dry_run: bool = False) -> dict:
    """
    查询学习状态分流并执行导入
    """
    # 汇总所有需要处理的 voc_id
    all_voc_ids = set()
    voc_to_term = {}

    for term, vid in direct_matches.items():
        all_voc_ids.add(vid)
        voc_to_term.setdefault(vid, []).append(term)

    for word, vid in split_matches.items():
        all_voc_ids.add(vid)
        voc_to_term.setdefault(vid, []).append(word)

    if not all_voc_ids:
        return {
            "existing_voc_ids": set(),
            "new_add_voc_ids": set(),
            "advance_voc_ids": set(),
            "added_count": 0,
            "advanced_count": 0
        }

    # 批量查询已有学习记录
    rec_res = make_api_request("/study/query_study_records", {"voc_ids": list(all_voc_ids)}, token=token)
    records = rec_res.get("data", {}).get("records", [])
    existing_voc_ids = {rec["voc_id"] for rec in records if "voc_id" in rec}

    new_add_voc_ids = all_voc_ids - existing_voc_ids
    advance_voc_ids = all_voc_ids & existing_voc_ids

    added_count = 0
    advanced_count = 0

    if not dry_run:
        # 1. 新词添加至待背 (advance=false)
        if new_add_voc_ids:
            words_payload = [{"id": vid} for vid in new_add_voc_ids]
            add_res = make_api_request("/study/add_words", {"words": words_payload, "advance": False}, token=token)
            added_count = add_res.get("data", {}).get("added_count", len(new_add_voc_ids))

        # 2. 已有词提升为提前复习
        if advance_voc_ids:
            adv_res = make_api_request("/study/advance_study", {"voc_ids": list(advance_voc_ids)}, token=token)
            advanced_count = adv_res.get("data", {}).get("advanced_count", len(advance_voc_ids))
    else:
        added_count = len(new_add_voc_ids)
        advanced_count = len(advance_voc_ids)

    return {
        "existing_voc_ids": existing_voc_ids,
        "new_add_voc_ids": new_add_voc_ids,
        "advance_voc_ids": advance_voc_ids,
        "added_count": added_count,
        "advanced_count": advanced_count
    }


def format_report_text(direct_matches: dict, phrase_splits: dict, split_matches: dict, 
                       unrecognized: list, result: dict, dry_run: bool,
                       phrase_stopwords: dict = None, phrase_companions: dict = None) -> str:
    """
    格式化生成清晰的控制台 Markdown/文本分类报告
    """
    phrase_stopwords = phrase_stopwords or {}
    phrase_companions = phrase_companions or {}
    new_add_vids = result["new_add_voc_ids"]
    advance_vids = result["advance_voc_ids"]

    # 分类直接词条
    direct_new = []
    direct_advance = []
    for term, vid in direct_matches.items():
        if vid in new_add_vids:
            direct_new.append(term)
        elif vid in advance_vids:
            direct_advance.append(term)

    # 分类核心关键词词条（去重汇总）
    split_new = []
    split_advance = []
    for word, vid in split_matches.items():
        if vid in new_add_vids:
            split_new.append(word)
        elif vid in advance_vids:
            split_advance.append(word)

    lines = []
    lines.append("=" * 46)
    if dry_run:
        lines.append("🔍 墨墨背单词导入报告 【预览模式 - 未实际写入】")
    else:
        lines.append("🚀 墨墨背单词导入完成")
    lines.append("=" * 46)

    # 1. 新加待背
    total_new = len(direct_new) + len(split_new)
    lines.append(f"\n📌 新加待背 ({total_new} 个):")
    if direct_new:
        for t in direct_new:
            lines.append(f"  • {t}")
    if split_new:
        for w in split_new:
            lines.append(f"  • {w} (来自词组核心词)")
    if total_new == 0:
        lines.append("  (无)")

    # 2. 提前复习
    total_advance = len(direct_advance) + len(split_advance)
    lines.append(f"\n🔄 提前复习 ({total_advance} 个):")
    if direct_advance:
        for t in direct_advance:
            lines.append(f"  • {t}")
    if split_advance:
        for w in split_advance:
            lines.append(f"  • {w} (来自词组核心词)")
    if total_advance == 0:
        lines.append("  (无)")

    # 3. 词组核心关键词提取明细
    if phrase_splits:
        lines.append(f"\n✂️ 未整词收录的词组 → 提取核心关键词 ({len(phrase_splits)} 个词组):")
        for phrase, words in phrase_splits.items():
            breakdowns = []
            for w in words:
                vid = split_matches.get(w)
                if vid in new_add_vids:
                    st = "待背"
                elif vid in advance_vids:
                    st = "提前复习"
                else:
                    st = "未识别"
                breakdowns.append(f"{w} [{st}]")
            skipped_notes = []
            if phrase in phrase_companions and phrase_companions[phrase]:
                skipped_notes.append(f"跳过伴随词: {', '.join(phrase_companions[phrase])}")
            if phrase in phrase_stopwords and phrase_stopwords[phrase]:
                skipped_notes.append(f"跳过虚词: {', '.join(phrase_stopwords[phrase])}")
            suffix = f" （已{'; '.join(skipped_notes)}）" if skipped_notes else ""
            lines.append(f"  • \"{phrase}\" (未整句收录) → 核心词: {', '.join(breakdowns)}{suffix}")

    # 3.5 全虚词词组（无实义词，整条跳过）
    all_stopword_phrases = [p for p in phrase_stopwords if p not in phrase_splits]
    if all_stopword_phrases:
        lines.append(f"\n⏭️ 整条跳过的纯虚词词组 ({len(all_stopword_phrases)} 个):")
        for p in all_stopword_phrases:
            lines.append(f"  • \"{p}\" → 全部为虚词，不导入")

    # 4. 无法识别
    lines.append(f"\n❌ 无法识别 ({len(unrecognized)} 个):")
    if unrecognized:
        for u in unrecognized:
            lines.append(f"  • {u}")
    else:
        lines.append("  (无)")

    # 汇总条
    skipped_count = sum(len(v) for v in phrase_stopwords.values()) + sum(len(v) for v in phrase_companions.values())
    lines.append("\n" + "-" * 46)
    lines.append(f"📊 汇总统计: 新加待背 {total_new} | 提前复习 {total_advance} | 词组提取核心词 {len(phrase_splits)} | 跳过虚词/伴随词 {skipped_count} | 无法识别 {len(unrecognized)}")
    lines.append("=" * 46)

    return "\n".join(lines)


def format_report_json(direct_matches: dict, phrase_splits: dict, split_matches: dict, 
                       unrecognized: list, result: dict, dry_run: bool,
                       phrase_stopwords: dict = None, phrase_companions: dict = None) -> str:
    """
    格式化生成 JSON 结构化分类报告
    """
    phrase_stopwords = phrase_stopwords or {}
    phrase_companions = phrase_companions or {}
    new_add_vids = result["new_add_voc_ids"]
    advance_vids = result["advance_voc_ids"]

    direct_new = [t for t, vid in direct_matches.items() if vid in new_add_vids]
    direct_advance = [t for t, vid in direct_matches.items() if vid in advance_vids]
    split_new = [w for w, vid in split_matches.items() if vid in new_add_vids]
    split_advance = [w for w, vid in split_matches.items() if vid in advance_vids]

    phrase_detail = []
    for phrase, words in phrase_splits.items():
        w_list = []
        for w in words:
            vid = split_matches.get(w)
            if vid in new_add_vids:
                st = "new_added"
            elif vid in advance_vids:
                st = "advance_review"
            else:
                st = "unrecognized"
            w_list.append({"word": w, "status": st, "voc_id": vid})
        entry = {"phrase": phrase, "words": w_list}
        if phrase in phrase_stopwords:
            entry["skipped_stopwords"] = phrase_stopwords[phrase]
        if phrase in phrase_companions:
            entry["skipped_companions"] = phrase_companions[phrase]
        phrase_detail.append(entry)

    all_stopword_phrases = [p for p in phrase_stopwords if p not in phrase_splits]

    out = {
        "dry_run": dry_run,
        "summary": {
            "new_added_count": len(direct_new) + len(split_new),
            "advance_review_count": len(direct_advance) + len(split_advance),
            "phrase_splits_count": len(phrase_splits),
            "skipped_stopwords_count": sum(len(v) for v in phrase_stopwords.values()),
            "skipped_companions_count": sum(len(v) for v in phrase_companions.values()),
            "unrecognized_count": len(unrecognized)
        },
        "new_added": {
            "direct": direct_new,
            "from_phrases": split_new
        },
        "advance_review": {
            "direct": direct_advance,
            "from_phrases": split_advance
        },
        "phrase_splits": phrase_detail,
        "skipped_all_stopword_phrases": all_stopword_phrases,
        "unrecognized": unrecognized
    }
    return json.dumps(out, ensure_ascii=False, indent=2)


def cleanup_input_file(path: str) -> bool:
    """
    导入成功后清理输入 JSON 临时文件：删除文件本身，并尝试删除因此变空的临时父目录
    （最多向上两级，仅当目录为空时删除；绝不触碰当前工作目录及更上层）。
    清理失败静默忽略，不影响导入结果。返回是否删除了文件本身。
    """
    try:
        p = os.path.abspath(path)
        if not os.path.isfile(p):
            return False
        os.remove(p)
        parent = os.path.dirname(p)
        cwd = os.path.abspath(os.getcwd())
        for _ in range(2):
            # 到达工作目录或文件系统根时停止，只清理专属临时子目录
            if parent == cwd or os.path.dirname(parent) == parent:
                break
            try:
                os.rmdir(parent)  # 仅当目录为空时成功，非空抛 OSError
            except OSError:
                break
            parent = os.path.dirname(parent)
        return True
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description="考研英语篇末核心词汇校验与墨墨背单词自动化导入工具")
    parser.add_argument("--json", dest="json_input", help="词汇清单 JSON 字符串或文件路径")
    parser.add_argument("--query", dest="query_terms", help="直接调用墨墨接口查询指定单词或词组（逗号分隔）的收录状态与关键词解析")
    parser.add_argument("--token", dest="token", help="墨墨 API Token (若不指定则读取 MAIMEMOTOKEN 或 MAIMEMO_TOKEN 环境变量)")
    parser.add_argument("--validate-only", dest="validate_only", action="store_true", help="仅执行词汇去重、上限截断与格式校验，不请求墨墨 API")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help="预览模式，仅查询与分流，不执行写入操作")
    parser.add_argument("--format", dest="output_format", choices=["text", "json", "markdown", "check"], default=None,
                        help="输出格式：text/json（默认导入模式），check/markdown/json（--validate-only 模式）")
    parser.add_argument("--keep-json", dest="keep_json", action="store_true", help="导入成功后保留输入 JSON 文件（默认自动删除临时输入文件及其变空的父目录）")

    args = parser.parse_args()

    # 0. 若为单独查询模式 (--query)，直接调用接口并返回收录状态
    if args.query_terms:
        token = args.token or os.environ.get("MAIMEMOTOKEN") or os.environ.get("MAIMEMO_TOKEN")
        if not token:
            print("[ERROR] 缺少墨墨背单词 API Token。请在环境变量中设置 MAIMEMOTOKEN 或通过 --token 参数传入。", file=sys.stderr)
            sys.exit(1)
        query_list = [t.strip() for t in re.split(r"[,，\n]", args.query_terms) if t.strip()]
        if not query_list:
            print("[ERROR] 请指定要查询的词条。", file=sys.stderr)
            sys.exit(1)
        direct_matches, phrase_splits, split_matches, unrecognized, phrase_stopwords, phrase_companions = resolve_vocabularies(query_list, token)
        print("=" * 46)
        print("🔍 墨墨词汇/词组收录状态与核心关键词查询")
        print("=" * 46)
        for q in query_list:
            if q in direct_matches:
                print(f"  • {q}: ✅ 整词已收录 (voc_id: {direct_matches[q]})")
            elif q in phrase_splits:
                kw = phrase_splits[q][0] if phrase_splits[q] else ""
                vid = split_matches.get(kw, "未找到")
                comp_info = f", 跳过伴随词: {', '.join(phrase_companions.get(q, []))}" if phrase_companions.get(q) else ""
                stop_info = f", 跳过虚词: {', '.join(phrase_stopwords.get(q, []))}" if phrase_stopwords.get(q) else ""
                print(f"  • {q}: ✂️ 未整词收录 → 提取核心关键词: {kw} (voc_id: {vid}{comp_info}{stop_info})")
            else:
                print(f"  • {q}: ❌ 未收录/无法识别")
        sys.exit(0)

    # 1. 读取与解析输入 JSON
    raw_data = None
    if args.json_input:
        if os.path.isfile(args.json_input):
            try:
                with open(args.json_input, "r", encoding="utf-8-sig") as f:
                    raw_data = json.load(f)
            except Exception as e:
                print(f"[ERROR] 读取输入 JSON 文件失败: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            try:
                raw_data = json.loads(args.json_input)
            except Exception as e:
                print(f"[ERROR] 解析 --json 参数字符串失败: {e}", file=sys.stderr)
                sys.exit(1)
    else:
        if not sys.stdin.isatty():
            content = sys.stdin.read().strip()
            if content:
                try:
                    raw_data = json.loads(content)
                except Exception as e:
                    print(f"[ERROR] 解析标准输入 JSON 失败: {e}", file=sys.stderr)
                    sys.exit(1)
            else:
                print("[ERROR] 未检测到标准输入内容，请通过 --json 或标准输入传入词汇清单 JSON。", file=sys.stderr)
                sys.exit(1)
        else:
            parser.error("必须通过 --json、--query 或标准输入传入词汇清单")

    # 2. 若为仅校验模式 (--validate-only)，无需 Token，直接输出校验结果
    if args.validate_only:
        try:
            deduped_dicts, _, duplicates, truncated, invalid = normalize_vocab_entries(raw_data)
        except Exception as e:
            print(f"[ERROR] 词汇清单格式错误: {e}", file=sys.stderr)
            sys.exit(1)

        fmt = args.output_format or "check"
        print(format_validation_report(deduped_dicts, duplicates, truncated, invalid, output_format=fmt))
        sys.exit(0)

    # 3. 检查并获取 Token
    token = args.token or os.environ.get("MAIMEMOTOKEN") or os.environ.get("MAIMEMO_TOKEN")
    if not token:
        print("[ERROR] 缺少墨墨背单词 API Token。请在环境变量中设置 MAIMEMOTOKEN 或通过 --token 参数传入。", file=sys.stderr)
        sys.exit(1)

    # 4. 解析与归一化词汇
    try:
        terms, term_keywords = parse_input_vocab(raw_data)
    except Exception as e:
        print(f"[ERROR] 词汇清单格式错误: {e}", file=sys.stderr)
        sys.exit(1)

    if not terms:
        print("[WARNING] 输入词汇列表为空，无词条需要导入。", file=sys.stderr)
        sys.exit(0)

    # 5. 词汇查询与短语拆分兜底
    try:
        direct_matches, phrase_splits, split_matches, unrecognized, phrase_stopwords, phrase_companions = resolve_vocabularies(terms, token, term_keywords=term_keywords)
    except MaiMemoAPIError as e:
        print(f"[ERROR] 墨墨词汇解析失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 6. 学习记录状态分流与写入
    try:
        result = partition_and_import(direct_matches, phrase_splits, split_matches, token, dry_run=args.dry_run)
    except MaiMemoAPIError as e:
        print(f"[ERROR] 墨墨学习记录分流/写入失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 7. 输出报告
    out_fmt = args.output_format or "text"
    if out_fmt == "json":
        report = format_report_json(direct_matches, phrase_splits, split_matches, unrecognized, result, args.dry_run, phrase_stopwords, phrase_companions)
    else:
        report = format_report_text(direct_matches, phrase_splits, split_matches, unrecognized, result, args.dry_run, phrase_stopwords, phrase_companions)

    print(report)

    # 8. 自动清理临时输入文件（仅正式导入成功时；dry-run 与 --keep-json 例外）
    if (not args.dry_run and not args.keep_json
            and args.json_input and os.path.isfile(args.json_input)):
        if cleanup_input_file(args.json_input):
            print(f"\n🧹 已自动清理临时输入文件: {os.path.abspath(args.json_input)}")


if __name__ == "__main__":
    main()
