import subprocess
import json
import os
import shutil
from datetime import datetime
from openai import OpenAI

# ===== 配置区 =====
DEEPSEEK_API_KEY = "sk-XXXXXXXXXXXXXXXXXXXXXXXXXX"
DESKTOP_PATH = os.path.join(os.environ['USERPROFILE'], 'Desktop')
# =================

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com/v1")

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
    """获取桌面所有项目（文件+文件夹）的基本信息"""
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

# ===== 工具函数 =====
def list_folder(rel_path=""):
    target = os.path.join(DESKTOP_PATH, rel_path) if rel_path else DESKTOP_PATH
    if not os.path.exists(target) or not os.path.isdir(target):
        return f"错误：路径不存在或不是文件夹：{rel_path}"
    try:
        items = os.listdir(target)
        items = [f for f in items if not f.startswith('.') and f != 'desktop.ini']
        return items if items else "（该文件夹为空）"
    except Exception as e:
        return f"读取文件夹失败：{e}"

def get_file_info(filename):
    full_path = os.path.join(DESKTOP_PATH, filename)
    if not os.path.isfile(full_path):
        return f"错误：文件不存在：{filename}"
    try:
        size = os.path.getsize(full_path)
        ext = os.path.splitext(filename)[1]
        return f"文件名：{filename}，扩展名：{ext}，大小：{size} 字节"
    except Exception as e:
        return f"读取文件信息失败：{e}"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_folder",
            "description": "查看桌面下某个文件夹的内容（文件和子文件夹名称列表）。当你需要了解某个已有文件夹里有什么文件，以决定是否将当前文件放进去时，使用这个工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "rel_path": {
                        "type": "string",
                        "description": "相对于桌面的文件夹路径，如 '学习'、'学习/微积分'。留空表示查看桌面根目录。"
                    }
                },
                "required": ["rel_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_info",
            "description": "获取桌面上某个文件的基本信息（大小、扩展名等）。当你需要了解某个文件的更多细节时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "文件名（含扩展名）"
                    }
                },
                "required": ["filename"]
            }
        }
    }
]

def get_user_instructions():
    """
    获取用户输入的补充提示词（多行），以空行结束。
    返回字符串，如果用户直接回车则返回空字符串。
    """
    print("\n【可选】你可以输入一些补充提示词来指导AI如何分类文件。")
    print("例如：'将课件都放入 学习/课件'、'所有 PDF 放入 文档/PDF'")
    print("输入完成后，在空行处按回车结束输入。直接按回车跳过。(需要多按一次回车)")
    print("> ", end="")
    lines = []
    while True:
        line = input()
        if line == "":
            break
        lines.append(line)
    if lines:
        return "\n".join(lines)
    return ""

def ask_ai_with_tools(movable_files, user_instructions=""):
    """多轮对话：让 AI 自主决定调用哪些工具来获取信息，最终返回规划结果。"""
    file_desc = "\n".join([f"- {f['name']} (扩展名: {f['ext']})" for f in movable_files])

    system_prompt = f"""
你是一个桌面整理规划师。桌面上有以下普通文件需要整理：
{file_desc}

**用户补充指令**（请优先遵循，高于其他规则）：
{user_instructions if user_instructions else "（无）"}

**你的工作方式**：
1. 你可以随时调用 `list_folder` 工具来查看任意文件夹的内容。
2. 你可以随时调用 `get_file_info` 工具来获取某个文件的详细信息。
3. 你可以自主决定查看哪些文件夹——如果你觉得某个文件夹的名称与某些文件可能相关，就查看它里面有什么，然后再做判断。
4. 你只需要看那些"可能相关"的文件夹，不需要全部查看。

**核心目标**：为每个文件规划一个目标路径（相对于桌面的路径），优先使用已存在的文件夹。

**规则**：
- 相同主题/课程的文件归入同一目录。
- 如果某个已有文件夹里已经有类似主题的文件，优先把新文件放进去。
- 如果没有合适的已有文件夹，可以新建（第一层从：工作、学习、图片、文档、软件工具、影音媒体、临时杂项、私人备份 中选择）。
- 文件本身不能是文件夹，不能是软件（.exe/.msi等）。

**最终输出**：当你认为信息已经足够时，返回一个纯JSON对象，格式为：
{{"plan": {{"文件名1": "目标路径1", "文件名2": "目标路径2"}}, "reasoning": "简要说明你的决策思路"}}
如果某个文件无法归类，路径设为空字符串。

注意：在最终输出之前，你可以多次调用工具来探索。开始吧！
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "请开始规划这些文件的去向。你可以先查看桌面根目录，然后根据需要深入查看相关文件夹。"}
    ]

    max_rounds = 10
    for _ in range(max_rounds):
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.2
        )

        assistant_msg = response.choices[0].message
        messages.append(assistant_msg)

        if not assistant_msg.tool_calls:
            content = assistant_msg.content
            try:
                if content:
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0].strip()
                    elif "```" in content:
                        content = content.split("```")[1].split("```")[0].strip()
                    result = json.loads(content)
                    if "plan" in result:
                        return result["plan"]
                    else:
                        return result
                else:
                    continue
            except json.JSONDecodeError:
                log_message(f"AI 最终输出解析失败：{content[:200]}", "ERROR")
                return {}
            break

        for tool_call in assistant_msg.tool_calls:
            func_name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            log_message(f"[AI 调用工具] {func_name}({args})")

            if func_name == "list_folder":
                result = list_folder(args.get("rel_path", ""))
            elif func_name == "get_file_info":
                result = get_file_info(args.get("filename", ""))
            else:
                result = f"未知工具：{func_name}"

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": str(result)
            })
            log_message(f"[工具返回] {result[:200]}")

    log_message("AI 对话达到最大轮次或未返回有效规划", "ERROR")
    return {}

def main():
    global LOG_FILE_PATH

    # ===== 第一步：先询问用户是否有额外指令 =====
    print("\n===== 桌面归纳程序 =====")
    user_instructions = get_user_instructions()

    # ===== 第二步：初始化日志并记录用户指令 =====
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    LOG_FILE_PATH = os.path.join(DESKTOP_PATH, f"organize_log_{timestamp}.txt")

    log_message("桌面归纳程序启动")
    log_message(f"目标桌面路径: {DESKTOP_PATH}")
    if user_instructions:
        log_message(f"用户补充指令：{user_instructions}")

    # ===== 第三步：扫描桌面 =====
    log_message("正在使用 PowerShell 扫描桌面目录...")

    items = get_desktop_items_with_powershell()
    if not items:
        log_message("未能获取桌面项目列表，程序终止。", "ERROR")
        write_log_to_file()
        return

    BLOCK_KEYWORDS = ["don't touch", "dont touch", "别碰", "禁止动", "!!重要", "!!"]
    SOFTWARE_EXTS = {'.exe', '.msi', '.lnk', '.bat', '.cmd', '.com', '.scr', '.pif'}

    movable_files = []
    file_info_map = {}

    for item in items:
        name = item['Name']
        full_path = item['FullName']
        lower_name = name.lower()
        ext = item.get("Extension", "").lower()

        if name.startswith(".") or name == "desktop.ini":
            continue
        if item.get("Mode", "").startswith("d"):
            continue
        if ext in SOFTWARE_EXTS:
            continue
        if any(kw in lower_name for kw in BLOCK_KEYWORDS):
            continue

        movable_files.append({"name": name, "ext": ext})
        file_info_map[name] = full_path

    if not movable_files:
        log_message("没有可移动的普通文件，程序结束。")
        write_log_to_file()
        return

    log_message(f"共发现 {len(movable_files)} 个普通文件")
    log_message("正在与 AI 对话（AI 会自主决定查看哪些文件夹来辅助决策）...")

    plan = ask_ai_with_tools(movable_files, user_instructions)
    if not plan:
        log_message("AI 未返回有效规划，程序终止。", "ERROR")
        write_log_to_file()
        return

    stats = {"moved": 0, "ignored": 0, "errors": 0}

    log_message("开始执行移动...")
    for filename, target_path in plan.items():
        if filename not in file_info_map:
            continue
        full_path = file_info_map[filename]
        target_path = target_path.strip()
        if not target_path:
            log_message(f"[SKIP] {filename} 未分配路径，忽略。")
            stats["ignored"] += 1
            continue

        target_full_path = os.path.join(DESKTOP_PATH, target_path)
        try:
            os.makedirs(target_full_path, exist_ok=True)
        except Exception as e:
            log_message(f"[ERROR] 创建目录失败 {target_full_path}: {e}", "ERROR")
            stats["errors"] += 1
            continue

        dest = os.path.join(target_full_path, filename)
        try:
            if os.path.exists(dest):
                log_message(f"[WARN] 目标已存在，跳过移动 {dest}")
                stats["ignored"] += 1
            else:
                shutil.move(full_path, dest)
                log_message(f"[SUCCESS] {filename} -> {target_path}")
                stats["moved"] += 1
        except Exception as e:
            log_message(f"[ERROR] 移动 {filename} 失败: {e}", "ERROR")
            stats["errors"] += 1

    log_message("=" * 50)
    log_message("整理任务执行完毕，统计信息：")
    log_message(f"  成功移动：{stats['moved']} 项")
    log_message(f"  忽略（无路径或冲突）：{stats['ignored']} 项")
    log_message(f"  异常错误：{stats['errors']} 项")
    log_message("=" * 50)

    write_log_to_file()

if __name__ == "__main__":
    main()