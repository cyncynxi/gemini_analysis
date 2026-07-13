import os
import json
import google.generativeai as genai

# 1. 验证并初始化 Gemini API 密钥
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("❌ 错误：未能在环境变量中找到 GEMINI_API_KEY。请检查 GitHub Secrets 配置。")
    exit(1)

genai.configure(api_key=api_key)

# 2. 读取你的 materials.json
try:
    with open('materials.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
except Exception as e:
    print(f"❌ 读取 materials.json 失败: {e}")
    exit(1)

# 假设你的 materials.json 格式为：{"materials": [{"id": 1, "video_url": "...", "celeb": "冯唐"}, ...]}
materials_list = data.get("materials", [])

# 3. 筛选出“未处理”或“缺少AI拆解”的素材（避免重复调用浪费Token）
pending_materials = [m for m in materials_list if "ai_analysis" not in m or not m["ai_analysis"]]

if not pending_materials:
    print("✨ 所有素材已完成 AI 拆解，无须重复处理！")
    exit(0)

print(f"🚀 发现 {len(pending_materials)} 条新素材，正在启动 Gemini 智能深度拆解...")

# 使用专门处理高性价比多模态与超长上下文的 gemini-2.5-flash 模型
model = genai.GenerativeModel('gemini-2.5-flash')

# 4. 定制面向腾讯信息流明星带货的专业提示词（Prompt）
base_prompt = """
你是一位深谙腾讯广告（广点通）爆量逻辑的资深创意总监。请针对以下提供的明星/达人带货素材信息（包含视频链接或基本文本），进行全方位的爆量行为学深度拆解。

请务必严格按照以下 JSON 格式进行输出，不要包含任何 Markdown 标记（如 ```json ）。

期望的 JSON 输出结构示例：
{{
    "hook_analysis": "这里填写前3秒明星吸睛钩子的精妙之处，例如：利用明星专属人设反差强效留人",
    "five_steps": {{
        "step1_intro": "引流段：达人xx手持产品极速切入，完成黄金3s留存",
        "step2_painpoint": "痛点段：直击xx核心生活场景痛点，引发用户共鸣",
        "step3_display": "展示段：高光放大产品核心卖点与实测效果",
        "step4_trust": "信任段：借由明星声誉背书或工厂权威资质建立绝对信任",
        "step5_action": "行动段：限时买一送一/超低价福利临门一脚促转化"
    }},
    "tags": ["明星极速切入", "低价促单", "高情绪价值", "场景实测"]
}}
"""

# 5. 循环调用 API 处理
for item in pending_materials:
    video_url = item.get("video_url", "")
    celeb = item.get("celeb", "未知达人")
    category = item.get("cat2", "未知品类")
    
    print(f"📦 正在拆解达人【{celeb}】的【{category}】素材...")
    
    # 构建当前素材的请求上下文
    user_input = f"\n当前素材信息：\n- 明星/达人: {celeb}\n- 垂直类目: {category}\n- 素材播放链接: {video_url}\n"
    
    try:
        response = model.generate_content(base_prompt + user_input)
        response_text = response.text.strip()
        
        # 清理可能存在的无效格式
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]
        
        # 解析为结构化 JSON 并注入回原数据中
        analysis_json = json.loads(response_text)
        item["ai_analysis"] = analysis_json
        print(f"✅ 素材 {item.get('id')} 拆解成功！")
        
    except Exception as e:
        print(f"⚠️ 素材 {item.get('id')} 拆解失败，原因: {e}")
        # 如果报错，提供一个兜底结构，防止流程卡死
        item["ai_analysis"] = {
            "hook_analysis": "智能解析请求超时，请检查视频链接可访问性",
            "five_steps": {"step1_intro": "-", "step2_painpoint": "-", "step3_display": "-", "step4_trust": "-", "step5_action": "-"},
            "tags": ["解析待重试"]
        }

# 6. 将融合了 AI 深度拆解的数据，重新写回 materials.json
try:
    with open('materials.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    print("💾 成功将 AI 拆解数据写回 materials.json！")
except Exception as e:
    print(f"❌ 写回 materials.json 失败: {e}")
