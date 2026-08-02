import subprocess
import json
import os
import shutil
from datetime import datetime
from openai import OpenAI

#需要安装openai依赖。

# ===== 配置区 =====
DEEPSEEK_API_KEY = "你的DeepSeek api-key"
DESKTOP_PATH = os.path.join(os.environ['USERPROFILE'], 'Desktop')
# =================

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com/v1")

# 全局日志
LOG_ENTRIES = []
LOG_FILE_PATH = None

def log_message(msg, level="INFO"):
    global LOG_ENTRIES
    print(msg)
    LOG_ENTRIES.append(f"[{level}] {msg}")

def write_log_to_file():
    global LOG_FILE_PATH
    if not LOG_FILE_PATH:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        LOG_FILE_PATH = os.path.join(DESKTOP_PATH, f"organize_log_{timestamp}.txt")
    try:
        with open(LOG_FILE_PATH, 'w', encoding='utf-8') as f:
            f.write(f"桌面整理日志 - 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"目标桌面路径: {DESKTOP_PATH}\n")
            f.write("=" * 60 + "\n")
            for entry in LOG_ENTRIES:
                f.write(entry + "\n")
            f.write("=" * 60 + "\n")
            f.write("日志结束\n")
        print(f"\n[INFO] 整理日志已保存至: {LOG_FILE_PATH}")
    except Exception as e:
        print(f"[ERROR] 写入日志文件失败: {e}")

def get_desktop_items_with_powershell():
    ps_command = f'powershell -Command "Get-ChildItem -Path \'{DESKTOP_PATH}\' -Force -ErrorAction SilentlyContinue | Select-Object Name, FullName, Mode, Length, Extension, LastWriteTime | ConvertTo-Json -Compress"'
    try:
        raw_output = subprocess.check_output(ps_command, shell=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        log_message(f"PowerShell执行失败，返回码：{e.returncode}", "ERROR")
        return []
    if not raw_output:
        log_message("PowerShell返回空结果。", "WARN")
        return []
    decoded = None
    for encoding in ['gbk', 'utf-8']:
        try:
            decoded = raw_output.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        decoded = raw_output.decode('utf-8', errors='ignore')
        log_message("警告：无法准确解码，已忽略部分无效字符。", "WARN")
    try:
        data = json.loads(decoded)
    except json.JSONDecodeError:
        log_message(f"JSON解析失败，前500字符：\n{decoded[:500]}", "ERROR")
        return []
    if isinstance(data, dict):
        return [data]
    return data

def ask_ai_for_decision(item):
    """
    安全调用AI，仅处理普通文件（文件夹和软件文件已提前过滤）。
    """
    try:
        name = item['Name']
        ext = item.get("Extension", "").lower()
        length = item.get("Length")
        size_mb = (length / (1024 * 1024)) if length is not None else 0

        prompt = f"""
你是一个桌面整理AI。请根据当前文件名和扩展名，将其归类到以下八类之一（并生成子类路径）：
大类：工作、学习、图片、文档、软件工具、影音媒体、临时杂项、私人备份。

强制规则：
- .pdf, .docx, .xlsx, .txt → 含"合同""发票"归"工作/财务"；含"论文""笔记"归"学习/资料"；其余归"文档/通用"。
- .jpg, .png, .gif, .psd → 归"图片/生活照" 或 "图片/截图"（根据文件名判断）。
- .mp4, .avi, .mov → "影音媒体/视频"；.mp3, .wav → "影音媒体/音乐"。
- .zip, .rar, .7z → "软件工具/压缩包" 或 "临时杂项/待解压"。
- 其他（如 .html, .py, .cpp）→ 根据名称归入"学习/代码" 或 "文档/通用"。

输出必须为纯JSON，格式：{{"action": "move", "target_path": "大类/子类/细项", "reason": "简短理由"}}
target_path 不以斜杠开头。

当前项目信息：
- 名称：{name}
- 扩展名：{ext}
- 大小：{size_mb:.2f} MB
"""
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        log_message(f"  [AI内部错误] {e}", "ERROR")
        return {"action": "ignore", "target_path": "", "reason": f"AI异常：{str(e)}"}

def main():
    global LOG_FILE_PATH
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    LOG_FILE_PATH = os.path.join(DESKTOP_PATH, f"organize_log_{timestamp}.txt")

    log_message("桌面归纳程序启动")
    log_message(f"目标桌面路径: {DESKTOP_PATH}")
    log_message("正在使用 PowerShell 扫描桌面目录...")

    items = get_desktop_items_with_powershell()
    if not items:
        log_message("未能获取桌面项目列表，程序终止。", "ERROR")
        write_log_to_file()
        return

    # 收集现有文件夹名称（用于后续可能的更新，但实际我们不再移动文件夹）
    existing_folders = [it['Name'] for it in items if it.get("Mode", "").startswith("d")]
    log_message(f"扫描完成，共发现 {len(items)} 个项目，其中包含 {len(existing_folders)} 个文件夹。")
    log_message("开始逐项分析（文件夹和软件文件将保持原位）...")

    # 物理硬拦截关键词（针对文件名）
    BLOCK_KEYWORDS = ["don't touch", "dont touch", "别碰", "禁止动", "!!重要", "!!"]
    # 软件相关扩展名（不移动）
    SOFTWARE_EXTS = {'.exe', '.msi', '.lnk', '.bat', '.cmd', '.com', '.scr', '.pif'}

    stats = {"moved": 0, "ignored_folder": 0, "ignored_software": 0, "ignored_blocked": 0, "ai_ignore": 0, "errors": 0}

    for idx, item in enumerate(items, 1):
        name = item['Name']
        full_path = item['FullName']
        lower_name = name.lower()

        # 跳过系统隐藏文件
        if name.startswith(".") or name == "desktop.ini":
            continue

        log_message(f"({idx}/{len(items)}) 正在分析：{name}")

        # 1. 如果是文件夹，跳过（保持原位）
        if item.get("Mode", "").startswith("d"):
            log_message(f"  [SKIP] 文件夹保持原位。")
            stats["ignored_folder"] += 1
            continue

        # 2. 如果扩展名属于软件/可执行文件/快捷方式，跳过
        ext = item.get("Extension", "").lower()
        if ext in SOFTWARE_EXTS:
            log_message(f"  [SKIP] 软件/快捷方式保持原位。")
            stats["ignored_software"] += 1
            continue

        # 3. 物理硬拦截（文件名含保护关键词）
        if any(kw in lower_name for kw in BLOCK_KEYWORDS):
            log_message(f"  [PROTECT] 硬编码拦截：文件名含保护关键词，已跳过。")
            stats["ignored_blocked"] += 1
            continue

        # 4. 普通文件，调用AI决策
        decision = ask_ai_for_decision(item)
        action = decision.get("action", "ignore")
        target_path = decision.get("target_path", "").strip()
        reason = decision.get("reason", "无具体理由")

        if action == "ignore":
            log_message(f"  [AI SKIP] 已忽略。原因：{reason}")
            stats["ai_ignore"] += 1
            continue

        # 执行移动
        target_full_path = os.path.join(DESKTOP_PATH, target_path)
        try:
            os.makedirs(target_full_path, exist_ok=True)
        except Exception as e:
            log_message(f"  [ERROR] 创建目录失败：{e}", "ERROR")
            stats["errors"] += 1
            continue

        dest = os.path.join(target_full_path, name)
        try:
            if os.path.exists(dest):
                log_message(f"  [WARN] 目标位置已存在同名文件，跳过移动：{dest}")
                stats["ai_ignore"] += 1
            else:
                shutil.move(full_path, dest)
                log_message(f"  [SUCCESS] 已移动至分层目录：{target_path} (理由：{reason})")
                stats["moved"] += 1
        except Exception as e:
            log_message(f"  [ERROR] 移动失败：{e}", "ERROR")
            stats["errors"] += 1

        log_message("")  # 空行

    log_message("=" * 50)
    log_message("整理任务执行完毕，统计信息：")
    log_message(f"  成功移动普通文件：{stats['moved']} 项")
    log_message(f"  保持原位的文件夹：{stats['ignored_folder']} 项")
    log_message(f"  保持原位的软件/快捷方式：{stats['ignored_software']} 项")
    log_message(f"  物理关键词拦截：{stats['ignored_blocked']} 项")
    log_message(f"  AI决定忽略：{stats['ai_ignore']} 项")
    log_message(f"  异常错误：{stats['errors']} 项")
    log_message("=" * 50)

    write_log_to_file()

if __name__ == "__main__":
    main()