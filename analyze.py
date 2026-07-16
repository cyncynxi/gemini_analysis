"""
Gemini 素材批量拆解工具（JSON 版 —— 终极生产安全版）

相比上一版本优化：
  1. 完善 atomic_save 的异常捕获与清理机制，确保临时文件 100% 释放。
  2. 显式兼容 Pydantic 类型的 schema 传递，防止高版本 Pydantic 警告。
  3. 优化了终端日志的可读性，重试时提供更友好的提示。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pydantic import BaseModel, Field
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)
import google.generativeai as genai
from google.generativeai import types

# ============================================================
# 配置区
# ============================================================

# 输入/输出文件路径（与脚本同目录）
MATERIALS_JSON = Path(__file__).resolve().parent / "materials.json"

# 并发线程数（根据你账号的 RPM 上限调整，付费账号建议 5-10，免费账号建议 2-3）
MAX_WORKERS = 5

# 单条 API 调用的最大重试次数
MAX_RETRIES = 3

# 线程锁：确保多线程增量写入文件时，同一时间只有一个线程在操作文件
file_write_lock = threading.Lock()

# ============================================================
# Pydantic Schema
# ============================================================

class FiveSteps(BaseModel):
    """黄金5段式拆解结构"""

    step1_intro: str = Field(
        description="引流段：达人/明星如何切入，完成黄金3s留存"
    )
    step2_painpoint: str = Field(
        description="痛点段：直击核心生活场景或细分人群痛点，引发共鸣"
    )
    step3_display: str = Field(
        description="展示段：高光放大产品核心卖点、使用体验或实测效果"
    )
    step4_trust: str = Field(
        description="信任段：借由明星声誉背书、工厂资质、成分党科学佐证或大盘销量数据建立绝对信任"
    )
    step5_action: str = Field(
        description="行动段：限时买一送一、低价福利、优惠机制临门一脚促进转化"
    )


class AnalysisResult(BaseModel):
    """单条素材的 AI 拆解结果"""

    hook_analysis: str = Field(
        description="前3秒明星吸睛钩子的精妙之处拆解，如明星人设反差、视觉冲击或黄金痛点"
    )
    five_steps: FiveSteps = Field(description="黄金5段式拆解")
    tags: list[str] = Field(
        description="标签提取（如：明星反差、场景痛点、低价促单、情绪价值）"
    )


# 兜底结构常量
FALLBACK_ANALYSIS = {
    "hook_analysis": "智能解析出现异常",
    "five_steps": {
        "step1_intro": "无法解析",
        "step2_painpoint": "无法解析",
        "step3_display": "无法解析",
        "step4_trust": "无法解析",
        "step5_action": "无法解析",
    },
    "tags": ["解析待重试"],
}

# ============================================================
# 字段兼容辅助
# ============================================================

def _get_field(item: dict, *keys: str, default: str = "未知") -> str:
    """按优先级从多个可能的字段名中取值"""
    for k in keys:
        val = item.get(k)
        if val is not None:
            return str(val)
    return default

# ============================================================
# Gemini API 初始化
# ============================================================

api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
if not api_key:
    print("❌ 错误：未能在环境变量中找到 GEMINI_API_KEY。请检查 GitHub Secrets 或本地配置。")
    sys.exit(1)

genai.configure(api_key=api_key)

SYSTEM_INSTRUCTION = """
你是一位深谙腾讯广告（广点通）爆量逻辑的资深创意总监，擅长剖析日用百货等赛道中明星/达人素材的转化密码。
请针对提供的明星/达人带货素材信息（包含达人名称、垂直类目、素材链接），结合其带货品类，进行全方位的爆量行为学深度拆解。
你的分析应该严谨、具备实操价值，能够直接用于指导下一批视频剪辑。
"""

model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    system_instruction=SYSTEM_INSTRUCTION,
)

# ============================================================
# 带重试的 API 调用
# ============================================================

_retry_decorator = retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=2, min=2, max=30),  # 4s, 8s, 16s...
    before_sleep=before_sleep_log(logging.getLogger("GeminiRetry"), log_level=logging.WARNING), # 🔑 传入真正的 Logger 对象
    reraise=True,
)


@_retry_decorator
def _call_gemini(user_input: str, item_id: str) -> dict:
    """
    调用 Gemini Structured Outputs API，自带指数退避重试。
    """
    response = model.generate_content(
        user_input,
        generation_config=types.GenerationConfig(
            response_mime_type="application/json",
            response_schema=AnalysisResult,
            temperature=0.2,
        ),
        request_options={"timeout": 120.0},
    )
    return json.loads(response.text.strip())


def analyze_one(item: dict) -> tuple[dict, dict]:
    """
    拆解单条素材，返回 (更新后的 item, 日志信息)
    """
    celeb = _get_field(item, "celeb", "celebrity")
    category = _get_field(item, "cat2", "sub_segment", "marketing_obj_level2")
    item_id = _get_field(item, "id", "material_id", default="未命名ID")

    user_input = (
        f"请对以下素材进行拆解：\n"
        f"- 明星/达人: {celeb}\n"
        f"- 垂直类目/商品: {category}\n"
        f"- 素材播放链接: {item.get('video_url', '')}\n"
    )

    try:
        analysis_json = _call_gemini(user_input, item_id)
        item["ai_analysis"] = analysis_json
        return item, {
            "item_id": item_id,
            "celeb": celeb,
            "category": category,
            "status": "ok",
            "msg": f"✅ 素材 [{item_id}] 拆解成功！(达人: {celeb})",
        }
    except Exception as e:
        # 深拷贝避免脏数据
        fallback = json.loads(json.dumps(FALLBACK_ANALYSIS))
        fallback["hook_analysis"] = f"智能解析出现异常: {str(e)}"
        item["ai_analysis"] = fallback
        return item, {
            "item_id": item_id,
            "celeb": celeb,
            "category": category,
            "status": "fail",
            "msg": f"⚠️ 素材 [{item_id}] 拆解最终失败（已重试{MAX_RETRIES}次），原因: {e}",
        }


# ============================================================
# 线程安全的增量保存 + 原子写入
# ============================================================

def atomic_save(data: list[dict], path: Path) -> None:
    """
    原子写入：先写临时文件，再 os.replace 替换目标文件。
    """
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=".materials_"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp_path, str(path))
    except Exception as e:
        # 显式捕获异常并清理临时文件，随后抛出
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise e


def incremental_save(data: list[dict], path: Path) -> None:
    """增量保存：使用线程锁（Lock）保证并发写入时的线程安全"""
    with file_write_lock:
        atomic_save(data, path)


# ============================================================
# 主流程
# ============================================================

def main() -> None:
    # 1. 读取 materials.json
    try:
        with open(MATERIALS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"❌ 读取 materials.json 失败: {e}")
        sys.exit(1)

    if not isinstance(data, list):
        print("❌ 错误：materials.json 的根节点必须是一个 JSON 数组（List[dict]）。")
        sys.exit(1)

    materials_list = data

    # 2. 筛选待处理素材
    pending_materials = [
        m for m in materials_list if "ai_analysis" not in m or not m["ai_analysis"]
    ]

    if not pending_materials:
        print("✨ 所有素材已完成 AI 拆解，无须重复处理！")
        return

    total = len(pending_materials)
    print(f"🚀 发现 {total} 条新素材，正在启动 Gemini 智能深度拆解...")
    print(f"   并发数：{MAX_WORKERS}  |  单条最大重试：{MAX_RETRIES}")

    # 3. 并发处理
    success_count = 0
    fail_count = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # 提交所有任务
        future_to_item = {
            executor.submit(analyze_one, item): item for item in pending_materials
        }

        for future in as_completed(future_to_item):
            # 获取执行结果
            updated_item, log = future.result()

            # 打印日志
            print(f"  {log['msg']}")

            if log["status"] == "ok":
                success_count += 1
            else:
                fail_count += 1

            # 🔑 线程安全地增量保存
            try:
                incremental_save(materials_list, MATERIALS_JSON)
            except Exception as e:
                print(f"  ❌ 增量落盘失败: {e}")

    # 4. 最终汇总
    print(f"\n{'='*60}")
    print(f"📊 批量拆解完成")
    print(f"   成功：{success_count} 条")
    print(f"   失败：{fail_count} 条")
    print(f"   数据文件：{MATERIALS_JSON}")
    print(f"{'='*60}")

    # 5. 最终保存，确保完全同步
    try:
        incremental_save(materials_list, MATERIALS_JSON)
        print("💾 最终数据已安全落盘！")
    except Exception as e:
        print(f"❌ 最终写入失败: {e}")


if __name__ == "__main__":
    main()
