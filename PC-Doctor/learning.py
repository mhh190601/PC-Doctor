"""
电脑医生 - 自学习模块（联网增强版）
本地匹配失败时，自动从网络搜索答案并存入知识库
"""

import sqlite3
import jieba
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import os
import requests
from bs4 import BeautifulSoup
import re

DB_FILE = 'pc_doctor_knowledge.db'

def get_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.executescript('''
        CREATE TABLE IF NOT EXISTS knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            source TEXT DEFAULT 'user',
            weight REAL DEFAULT 1.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            knowledge_id INTEGER,
            is_helpful INTEGER,
            user_question TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS unanswered (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            ask_count INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    conn.commit()
    conn.close()

def load_knowledge_data():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, question, answer, weight FROM knowledge ORDER BY weight DESC")
    rows = cursor.fetchall()
    conn.close()
    return [(row['id'], row['question'], row['answer'], row['weight']) for row in rows]

def rebuild_vectorizer(knowledge_list):
    if not knowledge_list:
        return None, None
    questions = [item[1] for item in knowledge_list]
    tokenized = [' '.join(jieba.cut(q)) for q in questions]
    vectorizer = TfidfVectorizer()
    matrix = vectorizer.fit_transform(tokenized)
    return vectorizer, matrix

knowledge_cache = []
vectorizer = None
tfidf_matrix = None

def refresh_cache():
    global knowledge_cache, vectorizer, tfidf_matrix
    knowledge_cache = load_knowledge_data()
    vectorizer, tfidf_matrix = rebuild_vectorizer(knowledge_cache)

def match_best_answer(user_question):
    """优化匹配：对模糊提问进行提示，不直接返回长答案"""
    global knowledge_cache, vectorizer, tfidf_matrix
    if not knowledge_cache:
        refresh_cache()
    if not knowledge_cache or not vectorizer:
        return None, None, 0.0

    tokenized_input = ' '.join(jieba.cut(user_question))
    input_vec = vectorizer.transform([tokenized_input])
    similarities = cosine_similarity(input_vec, tfidf_matrix).flatten()
    
    best_index = similarities.argmax()
    best_score = similarities[best_index]
    best_id, question, answer, weight = knowledge_cache[best_index]

    # 设定一个比较严格的匹配阈值，比如 0.3
    # 低于这个分数，说明用户问题和知识库里的已知问题差距较大
    if best_score < 0.3:
        return None, None, best_score

    # 处理"太短"或"太模糊"的提问（例如：少于4个字）
    # 这些提问虽然匹配到了，但很可能不是用户真正想问的
    if len(user_question.strip()) < 4:
        return f"🤔 你想问的是不是「{question}」？请把问题描述得更详细一些，我才能给你更准确的解答。", best_id, best_score
    
    # 如果匹配到的答案与问题完全相同，说明是精确命中，直接返回
    if question == user_question:
        return answer, best_id, best_score
        
    # 正常匹配成功
    return answer, best_id, best_score

def record_feedback(knowledge_id, is_helpful, user_question):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO feedback (knowledge_id, is_helpful, user_question) VALUES (?, ?, ?)",
        (knowledge_id, is_helpful, user_question)
    )
    weight_change = 0.1 if is_helpful else -0.1
    cursor.execute(
        "UPDATE knowledge SET weight = MAX(0, weight + ?), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (weight_change, knowledge_id)
    )
    conn.commit()
    conn.close()
    refresh_cache()

def add_new_knowledge(question, answer, source='auto'):
    """添加新知识，source 可以是 'user' 或 'auto'"""
    conn = get_connection()
    cursor = conn.cursor()
    # 检查是否已存在相同问题
    cursor.execute("SELECT id FROM knowledge WHERE question = ?", (question,))
    if cursor.fetchone():
        conn.close()
        return False
    cursor.execute(
        "INSERT INTO knowledge (question, answer, source) VALUES (?, ?, ?)",
        (question, answer, source)
    )
    conn.commit()
    conn.close()
    # 清理未回答记录
    conn = get_connection()
    conn.execute("DELETE FROM unanswered WHERE question = ?", (question,))
    conn.commit()
    conn.close()
    refresh_cache()
    return True

def log_unanswered(question):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, ask_count FROM unanswered WHERE question = ?", (question,))
    row = cursor.fetchone()
    if row:
        cursor.execute("UPDATE unanswered SET ask_count = ask_count + 1 WHERE id = ?", (row['id'],))
    else:
        cursor.execute("INSERT INTO unanswered (question) VALUES (?)", (question,))
    conn.commit()
    conn.close()

# ================== 新增：联网搜索功能 ==================

def search_web(question, num_results=3):
    """多抓取 + 白名单过滤，确保拿到有用链接"""
    
    # 1. 判断是否电脑问题
    pc_keywords = [
        '电脑', '计算机', '笔记本', '台式', '系统', 'windows', 'win', 'mac', 'macOS',
        '卡', '慢', '卡顿', '死机', '蓝屏', '黑屏', '花屏', '重启', '关机', '开机',
        '内存', '硬盘', 'CPU', '显卡', '主板', '电源', '散热', '风扇', '驱动',
        '网络', '上网', 'WiFi', 'wifi', '宽带', '路由', 'DNS', 'IP',
        '病毒', '杀毒', '防火墙', '安全', '弹窗', '广告', '流氓', '软件',
        'C盘', 'D盘', '磁盘', '空间', '清理', '优化', '卡死', '闪退', '崩溃', '报错',
        '安装', '卸载', '更新', '升级', '浏览器', '输入法', '办公', '游戏',
        '声音', '没声音', '画面', '鼠标', '键盘', '屏幕', '分辨率'
    ]
    if not any(kw in question.lower() for kw in pc_keywords):
        return "👋 你好！我是电脑医生，只擅长回答电脑相关问题哦。\n\n请描述你的电脑遇到了什么问题，比如：\n• 电脑卡顿怎么办\n• C盘满了如何清理\n• 电脑蓝屏了怎么解决"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    query = f'{question} 解决方法'
    search_url = f'https://www.bing.com/search?q={requests.utils.quote(query)}'
    
    print(f'[搜索调试] 扩展搜索: {query}')

    try:
        resp = requests.get(search_url, headers=headers, timeout=10)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')

        items = soup.select('li.b_algo')
        if not items:
            print('[搜索调试] 未找到Bing结果')
            return None

        # 可信站点列表（只要链接里包含这些关键字，就认为是好内容）
        trusted_sites = [
            'csdn.net',          # CSDN
            'cnblogs.com',       # 博客园
            'jianshu.com',       # 简书
            'zhihu.com/question',# 知乎问答
            'zhidao.baidu.com',  # 百度知道
            'segmentfault.com',  # SegmentFault
            'answers.microsoft.com', # 微软社区
            'xiaobaixitong.com', # 小白系统
            'xitongcheng.com',   # 系统城
            'luyouqi.com',       # 路由器相关
            'tianyanma.com',     # 一些技术小站
            'jb51.net',          # 脚本之家
            'diannao.com',       # 电脑学习网
        ]
        
        # 黑名单（电商/厂商/新闻门户，坚决过滤）
        blocked_sites = [
            'zol.com.cn', 'jd.com', 'taobao.com', 'tmall.com', 'dell.com',
            'lenovo.com.cn', 'hp.com', 'asus.com.cn', 'ithome.com',
            'sogou.com', 'smzdm.com', 'toutiao.com'
        ]

        results = []
        # 多取一些候选（前15条）
        for item in items[:15]:
            title_tag = item.select_one('h2 a')
            if not title_tag:
                continue
            link = title_tag.get('href', '')
            title = title_tag.get_text(strip=True)

            # 先过黑名单
            if any(bad in link for bad in blocked_sites):
                continue

            # 再查白名单
            if not any(good in link for good in trusted_sites):
                continue

            desc_tag = item.select_one('.b_caption p')
            desc = desc_tag.get_text(strip=True) if desc_tag else ''

            if len(desc) > 15:
                results.append({'title': title, 'link': link, 'desc': desc[:300]})
                if len(results) >= num_results:
                    break

        if results:
            parts = ["💡 为你找到以下解决方案：\n"]
            for i, r in enumerate(results, 1):
                parts.append(f"{i}. 📌 {r['title']}")
                parts.append(f"   📖 {r['desc']}")
                parts.append(f"   🔗 {r['link']}\n")
            return "\n".join(parts)
        else:
            print('[搜索调试] 白名单过滤后无结果，尝试打印前3条原始链接...')
            for i, item in enumerate(items[:3]):
                a = item.select_one('h2 a')
                if a:
                    print(f'  原始结果: {a.get_text(strip=True)} -> {a.get("href")}')
            return None

    except Exception as e:
        print(f'[搜索调试] 出错: {str(e)}')
        return None

def search_and_learn(question):
    """
    联网搜索答案，并将结果自动存入知识库
    返回整理好的答案文本
    """
    # 先搜
    answer = search_web(question)
    
    # 如果搜到了，自动存入知识库
    if answer and not answer.startswith("⚠️"):
        add_new_knowledge(question, answer, source='auto')
    
    return answer

def import_from_json(json_file):
    import json
    if not os.path.exists(json_file):
        return False
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    conn = get_connection()
    cursor = conn.cursor()
    for item in data:
        cursor.execute("SELECT id FROM knowledge WHERE question = ?", (item['question'],))
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO knowledge (question, answer, source) VALUES (?, ?, 'import')",
                (item['question'], item['answer'])
            )
    conn.commit()
    conn.close()
    refresh_cache()
    return True