"""Private, deterministic demand hints. No discovery, transport or execution."""
from __future__ import annotations
import re

ALIASES = {
    "translate": ("翻译", "translate", "translation"),
    "inspect": ("盘点", "结构分析", "词频", "inspect"),
    "summarize": ("摘要", "总结", "summarize", "summary"),
    "extract": ("提取", "抽取", "extract"),
    "code": ("代码", "编程", "python", "code"),
    "image": ("图片", "绘图", "海报", "image"),
    "audio": ("音频", "语音", "audio"),
    "video": ("视频", "video"),
}

def interpret(body):
    if not isinstance(body, dict) or set(body) - {"text", "known_skills"}:
        raise ValueError("INVALID_DEMAND_REQUEST")
    text, known = body.get("text"), body.get("known_skills", [])
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
        raise ValueError("INVALID_DEMAND_TEXT")
    if not isinstance(known, list) or len(known) > 256 or any(not isinstance(s, str) or len(s)>96 for s in known):
        raise ValueError("INVALID_DEMAND_SKILLS")
    lower = text.lower(); skills = []
    for skill in known:
        terms = ALIASES.get(skill, (skill,))
        if any(term.lower() in lower for term in terms): skills.append(skill)
    task = {"keywords": re.findall(r"[a-z0-9_]{2,32}|[\u4e00-\u9fff]{2,12}", lower)[:24]}
    for word, mode in (("json", "application/json"), ("文本", "text/plain"), ("图片", "image/png"),
                       ("音频", "audio/wav"), ("视频", "video/mp4")):
        if word in lower: task["output_mode"] = mode; break
    if "中文" in text: task["language"] = "zh-CN"
    elif "英文" in text or "english" in lower: task["language"] = "en"
    return {"task": task, "suggested_skills": skills, "state": "SUGGESTION",
            "notice": "本机按词语生成条件，尚未发现或调用服务；请确认能力和条件，未识别的要求仍需人工判断。"}
