"""
高情商本地陪聊模块（方案A+）
- 纯本地、零依赖、毫秒级（规则匹配+模板组合，<5ms）
- 三大能力：emotion_analyzer（情绪识别）/ context_memory（本地记忆）/ response_composer（动态组合）
- 情商原则：先共情后解决；不否定用户；适度自嘲；真诚第一；记住用户但不过界；引导不强迫
集成关系：
  - main.ai_diagnose 在「非纯电脑问题」时调用 compose_reply()；source 标记 'empathy'
  - main.ai_diagnose 在「电脑问题 + 情绪」同时出现时，先取 empathy_intro() 作共情前缀，再走正常诊断
"""
import os
import json
from datetime import datetime

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_EMOTION_PATH = os.path.join(_BASE_DIR, "emotion_rules.json")
_PROFILE_PATH = os.path.join(_BASE_DIR, "user_profile.json")
_TEMPLATE_PATH = os.path.join(_BASE_DIR, "response_templates.json")

# 复用 offtopic 的电脑关键词判定，避免重复维护
from offtopic import is_pc_related, _PC_KEYWORDS  # pyright: ignore[reportMissingImports]

_PROFILE_FIELDS = ("identity", "device_type", "device_age_years", "software_attitude")


# ============ 1. emotion_analyzer ============
def _load_rules() -> dict:
    try:
        with open(_EMOTION_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _has_pc_keyword(text: str, rules: dict) -> bool:
    """检测用户输入是否包含电脑问题关键词（自包含，从 emotion_rules.json 读取）。"""
    t = text.lower()
    return any(kw in t for kw in rules.get("pc_keywords", []))


def analyze_emotion(text: str):
    """子任务1.1：情绪识别 + 电脑问题检测，输出结构化结果。

    返回 dict：
      {
        "emotion":   anger/sadness/anxiety/joy/boredom/complaint/neutral,
        "label":     情绪中文标签,
        "intensity": low/mid/high,
        "is_pc":     是否涉及电脑问题 (bool),
      }
    匹配优先级：情绪词 > 电脑词 > 默认
      - 有情绪词 → 使用该情绪（is_pc 同时按关键词判定）
      - 无情绪词但有电脑词 → neutral + is_pc=True
      - 都没有 → neutral + is_pc=False
    """
    if not text:
        return {"emotion": "neutral", "label": "平静", "intensity": "low", "is_pc": False}
    t = text.lower()
    rules = _load_rules()
    # 1) 情绪词检测（取权重最高的情绪）
    best, best_w = "neutral", 0
    for emo, cfg in rules.get("emotions", {}).items():
        w = 0
        for tri in cfg.get("triggers", []):
            if tri["kw"].lower() in t:
                w += tri["weight"]
        if w > best_w:
            best, best_w = emo, w
    # 2) 电脑词检测
    is_pc = _has_pc_keyword(t, rules)
    # 3) 强度分级（按情绪总权重）
    if best_w >= 5:
        intensity = "high"
    elif best_w >= 3:
        intensity = "mid"
    elif best_w >= 1:
        intensity = "low"
    else:
        intensity = "low"
        best = "neutral"
    label = rules.get("emotions", {}).get(best, {}).get("label", "平静")
    return {"emotion": best, "label": label, "intensity": intensity, "is_pc": is_pc}


# ============ 2. context_memory ============
class ContextMemory:
    """本地用户画像读写：自动从对话抽取特征，持久化到 user_profile.json。"""

    def __init__(self, path: str = _PROFILE_PATH):
        self.path = path
        self.data = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                raise ValueError
            return d
        except Exception:
            return {
                "identity": "", "device_type": "", "device_age_years": None,
                "software_attitude": "", "recent_topics": [], "last_updated": ""
            }

    def _save(self):
        self.data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def extract(self, text: str):
        """从用户输入抽取用户特征并写入记忆（轻量，仅在命中时更新）。"""
        if not text:
            return
        t = text.lower()
        changed = False
        try:
            with open(_TEMPLATE_PATH, "r", encoding="utf-8") as f:
                tmpl = json.load(f)
            ex = tmpl.get("extraction_rules", {})
            # 身份
            for kw, val in ex.get("identity", {}).items():
                if kw in t and self.data.get("identity") != val:
                    self.data["identity"] = val
                    changed = True
                    break
            # 设备类型
            for kw, val in ex.get("device_type", {}).items():
                if kw in t and self.data.get("device_type") != val:
                    self.data["device_type"] = val
                    changed = True
                    break
            # 使用年限
            for item in ex.get("device_age", []):
                if item["kw"] in t:
                    yrs = item["years"]
                    if self.data.get("device_age_years") != yrs:
                        self.data["device_age_years"] = yrs
                        changed = True
                    break
            # 软件态度
            for kw, val in ex.get("software_attitude", {}).items():
                if kw in t and self.data.get("software_attitude") != val:
                    self.data["software_attitude"] = val
                    changed = True
                    break
        except Exception:
            pass
        if changed:
            self._save()

    def push_topic(self, text: str):
        """记录近期话题关键词（仅电脑相关时记录），保留最近5条。"""
        if not is_pc_related(text):
            return
        kw = [k for k in _PC_KEYWORDS if k in text.lower()]
        if not kw:
            return
        topics = self.data.setdefault("recent_topics", [])
        for k in kw:
            if k in topics:
                topics.remove(k)
            topics.append(k)
        self.data["recent_topics"] = topics[-5:]
        self._save()

    def personalization(self) -> dict:
        """返回用于模板填充的个性化变量字典。"""
        d = self.data
        identity = d.get("identity", "") or ""
        device = d.get("device_type", "") or ""
        age = d.get("device_age_years")
        age_note = f"大概{age}年" if isinstance(age, int) else ""
        return {
            "identity": identity,
            "device": device,
            "age_note": age_note,
            "has_student": identity == "学生",
            "has_old": isinstance(age, int) and age >= 4,
        }


# ============ 3. response_composer ============
# 子任务1.4 情商规则（代码中显式体现，作为不可绕过的约束）
EMPATHY_RULES = {
    "R1_negative_must_empathize_first":
        "用户表达负面情绪(anger/sadness/anxiety)时，第一句必须是共情，绝不能直接给操作步骤。",
    "R2_thanks_no_deny":
        "用户表达感谢(joy)时，不否定用户感受，不出现'这很简单'等贬低性表述。",
    "R3_chitchat_no_push":
        "用户明显闲聊(boredom/complaint)时，不硬推电脑话题，先陪聊/道歉再软引导。",
    "R4_remember_and_quote":
        "记住用户特征(身份/设备/年限)，在合适时机自然引用，不过界不监视。",
    "R5_sincere_not_oily":
        "语气真诚第一、幽默第二，避免油腻和过度玩笑。",
}
# 负面情绪集合（触发 R1）
_NEGATIVE_EMOTIONS = ("anger", "sadness", "anxiety")
# 闲聊/抱怨集合（触发 R3）
_CHITCHAT_EMOTIONS = ("boredom", "complaint")
# 兜底共情句（R1：负面情绪却无模板共情时强制前置）
_FALLBACK_EMPATHY = {
    "anger": "我能理解，这确实让人抓狂。",
    "sadness": "别急，这事没你想的那么糟。",
    "anxiety": "别慌，我们一步步来，来得及。",
}


class ResponseComposer:
    """根据情绪 + 上下文动态组合回复（非随机选句），并强制应用情商规则。"""

    def __init__(self, template_path: str = _TEMPLATE_PATH):
        self.templates = self._safe_load(template_path)

    @staticmethod
    def _safe_load(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"templates": {}}

    def compose(self, text: str, emotion: str, memory: ContextMemory, is_pc: bool = None) -> str:
        """子任务1.3/1.4：按情绪选组 → 填充变量 → 强制应用情商规则。"""
        t = self.templates
        pers = memory.personalization()
        pc = is_pc if isinstance(is_pc, bool) else is_pc_related(text)

        # 选组：情绪强度 + 近期话题数 → 确定性索引（结果可复现，不随机选句）
        emotion_res = analyze_emotion(text)
        intensity = emotion_res["intensity"]
        rank = {"low": 0, "mid": 1, "high": 2}.get(intensity, 0)
        topic_count = len(memory.data.get("recent_topics", []))
        groups = t.get("templates", {}).get(emotion) or t.get("templates", {}).get("neutral", [])
        if not groups:
            return "我在呢，说说看？"
        idx = (rank + topic_count) % len(groups)
        grp = groups[idx]

        # 填充变量（从用户特征动态替换）
        def fill(s: str) -> str:
            if not s:
                return ""
            return (s.replace("{identity}", pers.get("identity", ""))
                     .replace("{device}", pers.get("device", ""))
                     .replace("{age_note}", pers.get("age_note", "")))

        empathy_txt = fill(grp.get("empathy", ""))
        sol = fill(grp.get("solution", ""))
        tail = fill(grp.get("tail", ""))

        # ---- R4：自然引用用户特征（个性化尾巴）----
        if not tail and pc:
            if pers.get("has_student"):
                tail = "学生党预算有限的话，加根内存条往往是最划算的升级。"
            elif pers.get("has_old"):
                tail = f"用了{pers.get('age_note', '好些')}的老伙计确实会开始吃力，平时多清理能续命不少。"

        # ---- R1：负面情绪第一句必须是共情，绝不直接给步骤 ----
        if emotion in _NEGATIVE_EMOTIONS:
            if not empathy_txt:
                empathy_txt = _FALLBACK_EMPATHY.get(emotion, "我在听，你慢慢说。")
            # 确保 solution 绝不前置于共情（empathy 始终排第一）
            sol = sol  # sol 已在 empathy 之后拼接，结构上保证

        # ---- R2：感谢时不否定用户感受（joy 不拼接任何否定/贬低表述）----
        if emotion == "joy":
            # 移除可能带否定意味的短语，保持纯粹正向
            for deny in ("这问题很简单", "你怎么连", "这么基础"):
                sol = sol.replace(deny, "")
                tail = tail.replace(deny, "")

        # ---- R3：闲聊/抱怨不硬推电脑话题 ----
        if emotion in _CHITCHAT_EMOTIONS:
            # 若 solution 含强指令式"请按以下步骤/第一步"，弱化为软引导
            if "请按以下步骤" in sol or "第一步" in sol:
                sol = "想聊点别的也行，或者你说说具体哪不对，我陪你捋。"

        # 组装：[情绪回应] + [问题解决/引导] + [个性化尾巴]（empathy 始终首位）
        parts = [p for p in (empathy_txt, sol, tail) if p]
        return "".join(parts)


# ============ 模块级接口 ============
_memory = None
_composer = None


def _get_memory() -> ContextMemory:
    global _memory
    if _memory is None:
        _memory = ContextMemory()
    return _memory


def _get_composer() -> ResponseComposer:
    global _composer
    if _composer is None:
        _composer = ResponseComposer()
    return _composer


def compose_reply(text: str) -> str:
    """非纯电脑问题时调用：返回高情商闲聊/共情回复。"""
    memory = _get_memory()
    memory.extract(text)  # 抽取用户特征
    res = analyze_emotion(text)
    reply = _get_composer().compose(text, res["emotion"], memory, res["is_pc"])
    if not reply:
        reply = "我在呢，说说看？"
    memory.push_topic(text)
    return reply


def empathy_intro(text: str) -> str:
    """电脑问题 + 情绪同时出现时调用：只返回共情前缀（取对应情绪组1的 empathy 段，不重复给解决方案）。"""
    memory = _get_memory()
    memory.extract(text)
    res = analyze_emotion(text)
    if res["emotion"] == "neutral":
        return ""
    groups = _get_composer().templates.get("templates", {}).get(res["emotion"], [])
    if not groups:
        return ""
    return groups[0].get("empathy", "")
