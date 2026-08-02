"""
电脑医生 - 自学习模块（联网增强版）
本地匹配失败时，自动从网络搜索答案并存入知识库
"""
import sqlite3
import os
import sys
import re
import json

# 延迟导入重依赖（避免缺少依赖时模块崩溃）
jieba = None
TfidfVectorizer = None
cosine_similarity = None
requests_lib = None
BeautifulSoup = None

def _ensure_jieba():
    global jieba
    if jieba is None:
        import jieba as _jieba
        jieba = _jieba

def _ensure_sklearn():
    global TfidfVectorizer, cosine_similarity
    if TfidfVectorizer is None:
        from sklearn.feature_extraction.text import TfidfVectorizer as _Tfidf
        from sklearn.metrics.pairwise import cosine_similarity as _cos_sim
        TfidfVectorizer, cosine_similarity = _Tfidf, _cos_sim

def _ensure_requests():
    global requests_lib
    if requests_lib is None:
        import requests as _req
        requests_lib = _req

def _ensure_bs4():
    global BeautifulSoup
    if BeautifulSoup is None:
        from bs4 import BeautifulSoup as _BS
        BeautifulSoup = _BS

def _knowledge_db_path():
    """与 ai_engine.KnowledgeBridge 共用同一可写路径，避免打包后只读写入失败及双写数据分裂（bug #1/#2）"""
    if getattr(sys, 'frozen', False):
        appdata = os.environ.get('APPDATA') or os.path.expanduser('~')
        folder = os.path.join(appdata, '电脑医生')
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, "pc_doctor_knowledge.db")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "pc_doctor_knowledge.db")

DB_FILE = _knowledge_db_path()

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

    # 兼容升级：为旧表添加 tags、severity、reference 字段
    try:
        cursor.execute("ALTER TABLE knowledge ADD COLUMN tags TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # 字段已存在
    try:
        cursor.execute("ALTER TABLE knowledge ADD COLUMN severity TEXT DEFAULT '中'")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE knowledge ADD COLUMN reference TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

    # 启动时自动导入结构化知识库 v2（含来源/标签/风险等级），避免重复导入
    _auto_import_v2_knowledge()

def _auto_import_v2_knowledge():
    """启动时自动将 knowledge_base_v2.json 导入 SQLite（跳过已存在的问题）"""
    try:
        v2_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge_base_v2.json")
        if os.path.exists(v2_path):
            import_from_json(v2_path)
            logger.info("已自动导入 knowledge_base_v2.json")
    except Exception as e:
        logger.warning(f"自动导入 knowledge_base_v2.json 失败: {e}")

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
    _ensure_jieba()
    _ensure_sklearn()
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
    """优化匹配：引入核心关键词加权，精准定位用户意图"""
    global knowledge_cache, vectorizer, tfidf_matrix
    if not knowledge_cache:
        refresh_cache()
    if not knowledge_cache or not vectorizer:
        return None, None, 0.0

    # 1. 先尝试核心关键词匹配（最高优先级）
    # 提取用户问题中的核心故障词
    _ensure_jieba()
    core_keywords = ['蓝屏', '死机', '黑屏', '花屏', '重启', '关机', '卡顿', '卡', '慢', '弹窗', '广告', '没声音', '声音', '网络', '上网', 'C盘', '磁盘', '清理', '卸载', '开机', '蓝屏代码', '报错', '崩溃', '闪退']
    
    user_words = set(jieba.cut(user_question))
    matched_keywords = [kw for kw in core_keywords if kw in user_words]
    
    # 如果有核心关键词命中，优先找知识库中包含同样关键词的问题
    if matched_keywords:
        for idx, (kid, question, answer, weight) in enumerate(knowledge_cache):
            if any(kw in question for kw in matched_keywords):
                # 只要问题里包含任一关键词，直接返回，跳过相似度计算
                return answer, kid, 1.0  # 返回最高置信度

    # 2. 如果没有关键词命中，回退到原有的 TF-IDF 相似度匹配
    _ensure_sklearn()
    tokenized_input = ' '.join(user_words)
    input_vec = vectorizer.transform([tokenized_input])
    similarities = cosine_similarity(input_vec, tfidf_matrix).flatten()
    
    best_index = similarities.argmax()
    best_score = similarities[best_index]
    best_id, question, answer, weight = knowledge_cache[best_index]

    if best_score < 0.3:
        return None, None, best_score
        
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

def add_new_knowledge(question, answer, source='auto', tags='', severity='中', reference=''):
    """添加新知识，source 可以是 'user' 或 'auto'，tags 为逗号分隔的标签"""
    conn = get_connection()
    cursor = conn.cursor()
    # 检查是否已存在相同问题
    cursor.execute("SELECT id FROM knowledge WHERE question = ?", (question,))
    if cursor.fetchone():
        conn.close()
        return False
    cursor.execute(
        "INSERT INTO knowledge (question, answer, source, tags, severity, reference) VALUES (?, ?, ?, ?, ?, ?)",
        (question, answer, source, tags, severity, reference)
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
    _ensure_requests()
    _ensure_bs4()
    
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
    search_url = f'https://www.bing.com/search?q={requests_lib.utils.quote(query)}'
    
    print(f'[搜索调试] 扩展搜索: {query}')

    try:
        resp = requests_lib.get(search_url, headers=headers, timeout=10)
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
        question = item.get('question', '')
        if not question:
            continue
        cursor.execute("SELECT id FROM knowledge WHERE question = ?", (question,))
        if not cursor.fetchone():
            answer = item.get('answer', '')
            source = item.get('source', 'import')
            tags = ','.join(item.get('tags', [])) if isinstance(item.get('tags'), list) else item.get('tags', '')
            severity = item.get('severity', '中')
            reference = item.get('reference', item.get('source', ''))
            cursor.execute(
                "INSERT INTO knowledge (question, answer, source, tags, severity, reference) VALUES (?, ?, ?, ?, ?, ?)",
                (question, answer, source, tags, severity, reference)
            )
    conn.commit()
    conn.close()
    refresh_cache()
    return True