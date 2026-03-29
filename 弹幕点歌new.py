import asyncio
import threading
import tkinter as tk
from selenium import webdriver
from bilibili_api import live, video, sync, Credential, search
from tkinter import font as tkfont
import re

import json
import os
import sys

import traceback
import logging

import queue
# 专门给 UI 用的同步队列
ui_update_queue = queue.Queue()

queue_lock = asyncio.Lock()
driver_lock = asyncio.Lock()

# 1. 强制禁用 Windows 的控制台控制处理器（防止高频 I/O 导致的信号崩溃）
os.environ['FOR_DISABLE_CONSOLE_CTRL_HANDLER'] = '1'

# 2. 关键：针对 Windows 环境优化异步策略
# 如果不加这个，打包后的程序在高并发 I/O 时极易触发运行时错误并闪退
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 3. 提高递归限制，防止 B 站 API 嵌套解析导致的溢出
sys.setrecursionlimit(5000)

# 获取 exe 所在目录
def get_base_path():
    if hasattr(sys, '_MEIPASS'):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

# 配置日志文件在 exe 同级目录
log_path = os.path.join(get_base_path(), "crash_report.log")

# 核心：将系统所有的 print 和报错都强制写进文件
class Logger(object):
    def __init__(self, filename):
        self.terminal = sys.stdout
        self.log = open(filename, "a", encoding="utf-8")
    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
        self.log.flush() # 实时刷新，确保闪退前能写入
    def flush(self):
        pass

sys.stdout = Logger(log_path)
sys.stderr = Logger(log_path) # 这一步最关键，捕获红色报错

logging.info("程序启动排查...")



os.chdir(os.path.dirname(os.path.abspath(sys.argv[0])))

# 获取程序运行目录（兼容打包后的 exe）
if getattr(sys, 'frozen', False):
    base_path = os.path.dirname(sys.executable)
else:
    base_path = os.path.dirname(os.path.abspath(__file__))

config_path = os.path.join(base_path, "config.json")

# 默认配置模板
default_config = {
    "ROOM_ID": "替换为直播间ID",
    "SESSDATA": "替换为SESSDATA",
    "HOST_UID": "替换为主播UID"
}

# 读取或创建配置文件
if not os.path.exists(config_path):
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(default_config, f, indent=4, ensure_ascii=False)
    #print(f"首次运行已生成配置文件: {config_path}")
    #print("请填入配置后重新运行程序。")
    sys.exit()

with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)

# 使用配置变量
ROOM_ID = config.get("ROOM_ID")
SESSDATA = config.get("SESSDATA")
credential = Credential(sessdata=SESSDATA)
HOST_UID = int(config.get("HOST_UID", 0))

# ==========================================

song_queue_data = []
song_list = []  # 用于界面显示的列表
current_song_text = ""
driver = None
skip_event = asyncio.Event() # 用于“切歌0”时立即切歌

def smart_truncate(text, max_px, current_font):
    """
    根据像素宽度截断字符串
    :param text: 原始歌名
    :param max_px: 允许的最大像素宽度 (例如 240)
    :param current_font: 窗口当前使用的字体对象
    """
    # 测量整个字符串的宽度
    if current_font.measure(text) <= max_px:
        return text
    
    # 逐个减少字符直到符合宽度
    temp_text = text
    while current_font.measure(temp_text + "...") > max_px and len(temp_text) > 0:
        temp_text = temp_text[:-1]
    
    return temp_text + "..."

def get_safe_song_list():
    # 使用之前定义的 queue_lock 确保读取时数据没在变
    # 但因为 after 里的函数不能是 async，我们这里用一个简单的 copy 技巧
    try:
        return list(song_list) # 快速复制一份快照
    except:
        return []
   
# --- UI 界面线程：显示点歌列表 ---
def create_display_window():
    root = tk.Tk()
    
    # 强制拦截所有 Tcl 内部错误，防止窗口静默消失
    def report_callback_exception(self, exc, val, tb):
        import traceback
        err = traceback.format_exception(exc, val, tb)
        with open("tk_error.log", "a") as f:
            f.write("".join(err))
    
    tk.Tk.report_callback_exception = report_callback_exception      
    
    root.title("点歌队列")
    
    # root.overrideredirect(True) # 注释掉这一行
    root.geometry("250x210")
    root.resizable(False, False)
    # 隐藏标题栏但保持在任务栏可见（这种模式通常能被采集）
    root.attributes("-alpha", 1.0) # 关掉透明，透明度会导致采集黑屏
    root.update_idletasks()
        
    # 设置深色背景颜色
    bg_color = "#222222" 
    root.configure(bg=bg_color)

    # --- 标题栏 ---
    title_bar = tk.Label(root, text="🎵 直播点歌机", font=("微软雅黑", 12), fg="white", bg="#333333")
    title_bar.pack(fill=tk.X)

    # --- 像素级滚动画布 (核心) ---
    # highlightthickness=0 去掉边框，确保纯净背景
    canvas = tk.Canvas(root, bg="#1A1A1A", height=40, highlightthickness=0)
    canvas.pack(fill=tk.X, pady=10)

    # 创建画布上的文字对象
    # anchor="nw" 表示以左上角为定位点
    text_id = canvas.create_text(280, 20, text="等待点歌中...", font=("微软雅黑", 12, "bold"), 
                                fill="#FFCC00", anchor="w")

    # --- 下方列表区域 (替换原本的 Listbox) ---
    # 使用 Text 文本框，因为它对宽度的填充比 Listbox 更彻底
    list_display = tk.Text(root, font=("微软雅黑", 10), 
                          bg="#1A1A1A", fg="#00FFCC", 
                          borderwidth=0, highlightthickness=0,
                          padx=10, pady=5, cursor="arrow")
    # 彻底填充左右
    list_display.pack(fill=tk.BOTH, expand=True)
    # 禁止用户手动输入
    list_display.config(state=tk.DISABLED)

    def scroll_logic():
        global current_song_text
        try:
            safe_list = list(song_list)
            
            has_songs = len(safe_list) > 0
            raw_content = f"▶{safe_list[0]}" if has_songs else "暂无歌曲播放，等待点歌..."
            # 拼接两遍，中间加空格
            display_title = raw_content + "    " + raw_content + "    " if safe_list else raw_content

            if display_title != current_song_text:
                current_song_text = display_title
                canvas.itemconfig(text_id, text=current_song_text)
                canvas.coords(text_id, 0, 20)

            if safe_list:
                canvas.move(text_id, -1, 0)
                pos = canvas.bbox(text_id)
                
                # 计算单段文本的宽度 (总宽除以2)
                total_width = pos[2] - pos[0]
                single_width = total_width / 2
                
                # 当移动距离超过单段宽度时，瞬间拉回 0
                if abs(pos[0]) >= single_width:
                    canvas.coords(text_id, 0, 20)
        except Exception:
            pass
        finally:
            root.after(35, scroll_logic)

    my_font = tkfont.Font(family="微软雅黑", size=9)

    # --- 4. 修改 UI 刷新逻辑 (安全读取) ---
    def update_list():
        """由 Tkinter 主线程定时调用，安全同步数据"""
        try:
            # 只有在收到信号或者定时检查时，才‘拷贝’一份列表快照
            # 这样即便异步线程正在修改 song_list，UI 线程拿到的也是安全的副本
            safe_list_snapshot = list(song_list) 
            
            list_display.config(state=tk.NORMAL)
            list_display.delete('1.0', tk.END)
            
            if len(safe_list_snapshot) > 1:
                waiting_list = safe_list_snapshot[1:]
                for i, name in enumerate(waiting_list, 1):
                    # 使用快照数据更新 UI
                    display_name = smart_truncate(name, 350, my_font)
                    list_display.insert(tk.END, f" {i:02d}. {display_name}\n")
        except Exception as e:
            pass 
        finally:
            list_display.config(state=tk.DISABLED)
            root.after(1000, update_list) # 每秒同步一次

    # --- 窗口拖动逻辑 ---
    def start_move(event): root.x, root.y = event.x, event.y
    def stop_move(event): root.geometry(f"+{root.winfo_x() + event.x - root.x}+{root.winfo_y() + event.y - root.y}")
    title_bar.bind("<Button-1>", start_move)
    title_bar.bind("<B1-Motion>", stop_move)

    # 启动循环
    scroll_logic()
    update_list()
    root.mainloop()

# --- 消费者：负责浏览器控制 (已优化版) ---
async def music_player_worker():
    await asyncio.sleep(5)
    global driver
    # 建议在外部定义全局变量: 
    # queue_lock = asyncio.Lock()
    # driver_lock = asyncio.Lock()

    try:
        # 使用 Service 包装并隐藏驱动控制台，减少打包后的不稳定因素
        from selenium.webdriver.chrome.service import Service
        # 若打包后找不到驱动，建议将 chromedriver.exe 放在 exe 同级目录
        driver = webdriver.Chrome() 
        driver.get("https://www.bilibili.com")
        driver.add_cookie({'name': 'SESSDATA', 'value': SESSDATA, 'domain': '.bilibili.com', 'path': '/'})
        driver.refresh()
    except Exception as e:
        with open("crash_log.txt", "a") as f: f.write(f"Driver Init Fail: {e}\n")
        return

    while True:
        # --- 1. 安全读取队列 (加锁) ---
        current_song = None
        async with queue_lock:
            if song_queue_data:
                # 仅拷贝数据，暂时不 pop，防止播放中途列表索引变动
                current_song = list(song_queue_data[0]) 
            
        if not current_song:
            try:
                # 检查 URL 避免重复请求 about:blank 导致浏览器卡死
                if driver.current_url != "about:blank":
                    driver.get("about:blank")
            except: pass
            await asyncio.sleep(2)
            continue

        bv_id, title, duration, p_index = current_song
        
        try:
            url = f"https://www.bilibili.com/video/{bv_id}?p={p_index}"
            
            # 操作浏览器前建议也加个锁，防止弹幕回调里的 driver.execute_script 冲突
            async with driver_lock:
                driver.get(url)
                await asyncio.sleep(2) 
                # 注入拦截脚本
                driver.execute_script('''
                    const autoBtn = document.querySelector('.bpx-player-ctrl-next-autoswitch input');
                    if (autoBtn && autoBtn.checked) autoBtn.click();
                    if (window.player && window.player.setOptions) {
                        window.player.setOptions({playlist: {auto_play: false}});
                    }
                ''')

            skip_event.clear()
            
            # --- 2. 核心监控循环 ---
            while not skip_event.is_set():
                try:
                    # 监控逻辑 (注意：这里如果频繁操作 driver，建议也包在锁里或减小频率)
                    state = driver.execute_script('''
                        const v = document.querySelector("video");
                        if (!v) return null;
                        return { ended: v.ended, currentTime: v.currentTime, duration: v.duration };
                    ''')
                    
                    if state and state['duration'] > 0:
                        # 提前拦截逻辑
                        if state['ended'] or (state['currentTime'] >= state['duration'] - 0.8):
                            driver.execute_script('const v = document.querySelector("video"); if(v) v.pause();')
                            break
                except:
                    break # 浏览器窗口关闭或卡死时跳出
                
                await asyncio.sleep(0.5) # 频率调至 0.5s，降低 CPU 和 I/O 压力

        except Exception as e:
            with open("crash_log.txt", "a") as f: f.write(f"Playback Error: {e}\n")
        finally:
            # --- 3. 关键：播放结束后的清理 (必须加锁) ---
            async with queue_lock:
                # 再次检查，防止在播放期间由于“切歌”导致队列已经被清空或更改
                if song_queue_data and song_queue_data[0][0] == bv_id:
                    song_queue_data.pop(0)
                if song_list:
                    song_list.pop(0)
            
            await asyncio.sleep(1) # 缓冲

async def get_valid_video(search_results):
    for item in search_results:
        bvid = item.get('bvid')
        v = video.Video(bvid=bvid)
        try:
            # 获取视频信息以验证是否存在
            info = await v.get_info()
            return bvid  # 验证通过，返回可用bvid
        except Exception as e:
            # 捕获到 -404 等错误则继续查找下一个
            #print(f"视频 {bvid} 无效，尝试下一个...")
            continue
    return None


# --- 生产者：监听弹幕 ---
room = live.LiveDanmaku(ROOM_ID, credential=credential)

@room.on('DANMU_MSG')
async def on_danmaku(event):
    try:
        # 1. 提取弹幕文本 (确保兼容不同版本的 info 结构)
        info = event['data']['info']       
        content = str(info[1]) if isinstance(info, list) else "" 
        try:
            privilege_type = info[7] 
        except (IndexError, TypeError):
            privilege_type = 0
        is_vip = privilege_type > 0
        
            
        if content.startswith("点歌 ") or content.startswith("插歌 "):
            # 1. 定义正则：强制要求 点歌/插歌 后面紧跟至少一个空格
            # 使用了非捕获组 (?:...) 来匹配关键词
            pattern = r"(BV[a-zA-Z0-9]+)(?:\s*[pP_](\d+))?"
            match = re.search(pattern, content, re.IGNORECASE)            

            if match:                
                bv_id = match.group(1) # 获取匹配到的 BV 号
                p_index = int(match.group(2) if match.group(2) else "1") # 没传P数默认第1P
                # 如果正则没搜到 BV 号，但以关键词+空格开头，走关键字搜索逻辑
            else:
                keyword = content[2:].strip()
                if keyword:
                    res = await search.search_by_type(keyword, search_type=search.SearchObjectType.VIDEO)
                    if res['result']:
                        bv_id = await get_valid_video(res['result']) # 取搜索结果最靠前的有效视频
                        p_index = 1                
                
            # 3. 异步获取视频信息
            try:
                v = video.Video(bvid=bv_id, credential=credential)
                v_info = await v.get_info()
                
                # 处理分 P 逻辑
                pages = v_info.get('pages', [])
                if not pages:
                    #print("❌ 该视频没有内容")
                    return
                
                # 校验 P 数是否合法
                if p_index > len(pages) or p_index < 1:
                    #print(f"❌ 视频只有 {len(pages)} P，你点的第 {p_index} P 不存在")
                    return

                target_page = pages[p_index - 1] # 数组下标从0开始
                part_title = target_page['part'] # 获取这一P的小标题
                
                # 组合最终标题：如果只有1P就用主标题，多P则加上小标题
                final_title = v_info['title'] if len(pages) == 1 else f"{v_info['title']} (P{p_index}-{part_title})"
                duration = target_page['duration'] # 分P的时长
                
                user_name = info[2][1]
                
                song_item = (bv_id, final_title, duration, p_index)
                
                # 4. 成功后入队                
                async with queue_lock: # 确保同一时间只有一个弹幕在修改队列    
                    if content.startswith("点歌"):
                        song_queue_data.append(song_item)
                        song_list.append(final_title)         
                        ui_queue.put("REFRESH") 
                        #print(f"📩 {user_name}点歌 {final_title}")               
                    elif is_vip and content.startswith("插歌"):
                        pos = 1 if len(song_queue_data) > 0 else 0
                        song_queue_data.insert(pos, song_item)
                        song_list.insert(pos, final_title)        
                        ui_queue.put("REFRESH")                         
                        #print(f"📩 {user_name}插歌 {final_title}")   
            except Exception as v_err:
                dummy = 0
                # 捕获特定的视频不存在或网络错误
                #print(f"❌ 视频 {bv_id} 无效或解析失败: {v_err}")
        else:     
            user_uid = info[2][0]
            if user_uid == HOST_UID and content.startswith("切歌"):
                match = re.match(r'^切歌\s*(\d+)$', content)
                if match:
                    try:
                        # 提取序号并转为索引 (用户输入的 1 对应 list[0])
                        index = int(match.group(1))
                        # --- 切歌0：停止当前播放 ---
                        async with queue_lock:
                            if index == 0:
                                if song_queue_data:
                                    #print(f"🛑 管理员停止了当前播放: {song_list[0]}")
                                    # 触发切歌事件，worker 会执行 finally 里的 pop(0)
                                    skip_event.set() 
                                #else:
                                    #print("⚠️ 当前没有正在播放的歌曲")
                    
                            # --- 切歌N：删除排队中的歌曲 ---
                            elif 1 <= index < len(song_list):
                                song_queue_data.pop(index)
                                removed_song = song_list.pop(index)
                                #print(f"【系统】已删除第 {index} 首歌曲：{removed_song}")
                                #print(f"当前队列：{song_list}")
                            #else:
                                #print(f"【错误】序号 {index} 超出队列范围")
                    except ValueError:
                        pass
            # if is_vip and content.startswith("插歌"):
                # match = BV_PATTERN.search(content)
                
                # if not match:
                    # #print(f"⚠️ 点歌格式错误，请发送：点歌 BVxxxxxxx")
                    # return # 格式不对直接退出，不影响后续
                    
                # bv_id = match.group() # 获取匹配到的 BV 号
                
                # # 3. 异步获取视频信息
                # try:
                    # v = video.Video(bvid=bv_id, credential=credential)
                    # v_info = await v.get_info()
                    # title = v_info['title']
                    # duration = v_info['duration']
                    # user_name = info[2][1]
                    
                    # # 4. 成功后入队
                    

                    # #print(f"📩 {user_name}点歌 {title}")
                # except Exception as v_err:
                    # # 捕获特定的视频不存在或网络错误
                    # #print(f"❌ 视频 {bv_id} 无效或解析失败: {v_err}")
                
    except Exception as e:
        # 顶层捕获，防止解析弹幕结构本身出错导致脚本崩溃
        dummy = 0
        #print(f"🚨 弹幕解析异常: {e}")

# --- 3. 异步逻辑包装函数 ---
def run_async_background():
    """在子线程中运行所有异步逻辑"""
    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    
    async def main_task():
        # 同时启动播放器和弹幕监听
        await asyncio.gather(
            music_player_worker(),
            room.connect()
        )
    
    try:
        new_loop.run_until_complete(main_task())
    except Exception as e:
        with open("async_crash.log", "a") as f:
            f.write(f"Background Error: {e}\n")



# --- 5. 程序总入口 ---
if __name__ == '__main__':
    # A. 启动异步子线程
    logic_thread = threading.Thread(target=run_async_background, daemon=True)
    logic_thread.start()

    # B. 主线程启动 UI (必须在 __main__ 的最后)
    try:
        create_display_window() # 确保此函数最后是 root.mainloop()
    except Exception as e:
        with open("ui_crash.log", "a") as f:
            f.write(f"UI Mainloop Error: {e}")