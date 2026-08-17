"""
本地陪聊模块（offtopic）
- 纯本地、零依赖、毫秒级响应
- 不加载任何大模型、不依赖网络
- 当问题非电脑相关时，返回轻松幽默、人格化的闲聊回复
集成关系：main.ai_diagnose 中 is_pc_related() 返回 False 时调用 OffTopicReplier.reply()
"""
import os
import json
import random

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_JSON_PATH = os.path.join(_BASE_DIR, "offtopic_responses.json")

# 命中以下任一关键词 → 判定为电脑相关问题，绝不走闲聊模块
_PC_KEYWORDS = [
    "电脑", "计算机", "笔记本", "台式", "系统", "windows", "win", "mac", "苹果",
    "linux", "安卓", "软件", "硬件", "驱动", "显卡", "主板", "内存", "硬盘",
    "cpu", "gpu", "电池", "网络", "wifi", "网线", "路由", "宽带", "上网", "断网",
    "文件", "文件夹", "磁盘", "c盘", "d盘", "分区", "格式化", "u盘", "优盘",
    "蓝屏", "死机", "黑屏", "卡顿", "卡死", "闪退", "崩溃", "重启", "关机", "开机",
    "病毒", "木马", "杀毒", "防火墙", "弹窗", "广告", "清理", "加速", "优化",
    "浏览器", "微信", "qq", "办公", "word", "excel", "ppt", "更新", "升级",
    "密码", "账号", "登录", "蓝牙", "打印机", "摄像头", "麦克风", "音响", "耳机",
    "游戏", "steam", "程序", "应用", "安装", "卸载", "报错", "错误码", "配置", "设置",
    "键盘", "鼠标", "屏幕", "显示器", "电源", "充电", "发热", "风扇", "噪音", "温度",
]

# 分类匹配顺序（优先级从高到低）
_CATEGORY_ORDER = [
    "greeting", "self_intro", "capability", "weather", "mood",
    "thanks", "praise", "complaint", "default",
]

# 话术库文件缺失/损坏时的硬编码兜底
_FALLBACK_REPLIES = [
    "这个问题我还没学会。不如问问你的电脑最近卡不卡？",
    "我主要负责电脑问题，不过偶尔也能陪你闲聊两句。",
    "我是电脑医生，咱聊点电脑的事儿？比如清C盘、杀病毒。",
]


# 主观/闲聊倾向词：命中后即便包含电脑关键词，也判定为非严肃电脑问题（走陪聊）
# 这些词通常表示用户在闲聊、问观点、求推荐、发牢骚，而非报告具体故障
_SUBJECTIVE_MARKERS = [
    "你觉得", "你认为", "你以为", "你说说", "你讲讲", "你看看", "你给",
    "最好", "最差", "推荐", "建议买", "选什么", "用什么好", "哪个好",
    "怎么样", "咋样", "好不好", "行不行", "可以吗", "喜欢", "讨厌",
    "觉得", "感觉", "以为", "猜", "说说看", "讲讲看", "聊", "闲聊",
    "你最喜欢", "你最", "你平时", "你一般", "你懂", "你会", "你能",
    "难用", "不好用", "垃圾", "失望", "真废", "太差", "好烂", "卡顿", "崩溃",
]

# 明确的故障/操作信号词：命中后即便含主观词，也判定为真实电脑问题
_HARDWARE_ISSUE_MARKERS = [
    "坏了", "坏了", "故障", "报错", "错误码", "蓝屏", "黑屏", "死机", "卡死",
    "开不了机", "开不了", "启动不了", "进不去", "连不上", "断网", "上不了网",
    "太慢", "很卡", "特别卡", "无法", "不能", "怎么修", "怎么办", "怎么解决",
    "怎么弄", "如何", "教程", "步骤", "方法", "清理", "杀毒", "加速", "优化",
    "重装", "卸载", "安装失败", "打不开", "闪退", "崩溃", "发热严重",
]


def is_pc_related(text: str) -> bool:
    """判断是否为严肃的电脑相关问题（具体故障/操作），而非主观闲聊。

    命中任一电脑关键词 + 含明确故障/操作信号 → True（走诊断）
    命中电脑关键词但仅为观点/推荐/闲聊 → False（走陪聊）
    """
    if not text:
        return False
    t = text.lower()
    has_pc_kw = any(kw in t for kw in _PC_KEYWORDS)
    if not has_pc_kw:
        return False
    # 含明确故障/操作信号 → 真实电脑问题
    if any(m in t for m in _HARDWARE_ISSUE_MARKERS):
        return True
    # 含主观/闲聊倾向词，且无故障信号 → 视为闲聊
    if any(m in t for m in _SUBJECTIVE_MARKERS):
        return False
    return True


class OffTopicReplier:
    """闲聊匹配引擎：加载 JSON 话术库，按类别顺序匹配触发词并随机返回。"""

    def __init__(self, json_path: str = _JSON_PATH):
        self.data = self._safe_load(json_path)
        self.default_replies = self.data.get("default", {}).get("replies", _FALLBACK_REPLIES)

    @staticmethod
    def _safe_load(json_path: str) -> dict:
        """加载话术库，文件缺失或格式错误时回退到硬编码默认。"""
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "default" not in data:
                raise ValueError("话术库结构异常")
            return data
        except Exception:
            return {
                "default": {"patterns": [], "replies": _FALLBACK_REPLIES},
                "greeting": {"patterns": [], "replies": _FALLBACK_REPLIES},
                "self_intro": {"patterns": [], "replies": _FALLBACK_REPLIES},
                "capability": {"patterns": [], "replies": _FALLBACK_REPLIES},
                "weather": {"patterns": [], "replies": _FALLBACK_REPLIES},
                "mood": {"patterns": [], "replies": _FALLBACK_REPLIES},
                "thanks": {"patterns": [], "replies": _FALLBACK_REPLIES},
                "praise": {"patterns": [], "replies": _FALLBACK_REPLIES},
                "complaint": {"patterns": [], "replies": _FALLBACK_REPLIES},
            }

    def _match_category(self, text: str) -> str:
        """按优先级顺序匹配类别，返回命中的类别名；无命中返回 'default'。"""
        t = (text or "").lower()
        for cat in _CATEGORY_ORDER:
            if cat == "default":
                continue
            patterns = self.data.get(cat, {}).get("patterns", [])
            if any(p.lower() in t for p in patterns):
                return cat
        return "default"

    def reply(self, text: str) -> str:
        """返回一条闲聊回复（随机选取，毫秒级）。"""
        cat = self._match_category(text)
        replies = self.data.get(cat, {}).get("replies", self.default_replies)
        if not replies:
            replies = self.default_replies
        return random.choice(replies)


# 模块级单例，避免重复加载
_replier = None


def get_replier() -> OffTopicReplier:
    global _replier
    if _replier is None:
        _replier = OffTopicReplier()
    return _replier
