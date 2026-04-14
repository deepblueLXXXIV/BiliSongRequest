import random
import asyncio
import threading
import tkinter as tk
from selenium import webdriver
from bilibili_api import live, video, sync, Credential, search, Danmaku
from tkinter import font as tkfont
import re

import json
import os
import sys

import traceback
import logging

sys.setrecursionlimit(2000) # 适当调高限制

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
    "BILI_JCT": "替换为BILI_JCT",
    "HOST_UID": "替换为主播UID",
    "DEFAULT_PLAYLIST": [
        "替换为BV号",
        "替换为BV号",
        "替换为BV号，可无限添加"
    ],
    "MAX_DURATION_MIN": "替换为可点播视频的最长分钟数，自设播放列表无此限制"
}

# 读取或创建配置文件
if not os.path.exists(config_path):
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(default_config, f, indent=4, ensure_ascii=False)
    print(f"首次运行已生成配置文件: {config_path}")
    print("请填入配置后重新运行程序。")
    sys.exit()

with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)

# 使用配置变量
ROOM_ID = config.get("ROOM_ID")
SESSDATA = config.get("SESSDATA")
HOST_UID = int(config.get("HOST_UID"))
BILI_JCT = config.get("BILI_JCT")
credential = Credential(sessdata=SESSDATA, bili_jct=BILI_JCT)
DEFAULT_PLAYLIST = config.get("DEFAULT_PLAYLIST", [])
MAX_MIN = int(config.get("MAX_DURATION_MIN"))
MAX_SECONDS = MAX_MIN * 60
                 

# ==========================================
# --- 在函数外部定义，用于记忆播放历史 ---
history_queue = [] 
MAX_HISTORY = 5

song_queue_data = []
song_list = []  # 用于界面显示的列表
current_song_text = ""
driver = None
skip_event = asyncio.Event() # 用于“切歌0”时立即切歌
# 定义一个全局锁
skip_lock = asyncio.Lock()

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
    
# --- UI 界面线程：显示点歌列表 ---
def create_display_window():
    root = tk.Tk()
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
        
        raw_content = f"▶{song_list[0]}" if song_list else "暂无歌曲播放，等待点歌..."
        # 拼接两遍，中间加空格
        display_title = raw_content + "    " + raw_content + "    " if song_list else raw_content

        if display_title != current_song_text:
            current_song_text = display_title
            canvas.itemconfig(text_id, text=current_song_text)
            canvas.coords(text_id, 0, 20)

        if song_list:
            canvas.move(text_id, -1, 0)
            pos = canvas.bbox(text_id)
            
            # 计算单段文本的宽度 (总宽除以2)
            total_width = pos[2] - pos[0]
            single_width = total_width / 2
            
            # 当移动距离超过单段宽度时，瞬间拉回 0
            if abs(pos[0]) >= single_width:
                canvas.coords(text_id, 0, 20)

        root.after(30, scroll_logic)

    my_font = tkfont.Font(family="微软雅黑", size=9)

    def update_list():
        list_display.config(state=tk.NORMAL)
        list_display.delete('1.0', tk.END)
        
        # 获取窗口当前的实际宽度，减去左右边距 (例如 280-40=240)
        max_display_width = 350 
        
        waiting_list = song_list[1:]
        if waiting_list:
            for i, name in enumerate(waiting_list, 1):
                # 核心：按像素测量并截断
                display_name = smart_truncate(name, max_display_width, my_font)
                list_display.insert(tk.END, f" {i:02d}. {display_name}\n")

        list_display.config(state=tk.DISABLED)
        root.after(1000, update_list)

    # --- 窗口拖动逻辑 ---
    def start_move(event): root.x, root.y = event.x, event.y
    def stop_move(event): root.geometry(f"+{root.winfo_x() + event.x - root.x}+{root.winfo_y() + event.y - root.y}")
    title_bar.bind("<Button-1>", start_move)
    title_bar.bind("<B1-Motion>", stop_move)

    # 启动循环
    scroll_logic()
    update_list()
    root.mainloop()


async def send_broadcast(message):
    try:
        # 1. 实例化直播间对象
        room = live.LiveRoom(ROOM_ID, credential=credential)
        
        # 2. 关键：将字符串包装成 Danmaku 对象
        # 17.x 版本要求发送的是对象，而不是纯文本
        dm = Danmaku(text=message)
        
        # 3. 发送对象
        await room.send_danmaku(dm)
        print(f"💬 已发送弹幕反馈：{message}")
    except Exception as e:
        print(f"❌ 弹幕发送失败: {e}")
        
async def get_song_data(bv_id, p_index, credential, max_min_config, user_name, is_quiet):
    """
    is_quiet: 是否静默运行。True则不发送直播间弹幕提示。
    """
    try:
        v = video.Video(bvid=bv_id, credential=credential)
        v_info = await v.get_info()
        
        pages = v_info.get('pages', [])
        if not pages:
            if not is_quiet:
                await send_broadcast(f"@{user_name} ❌ 视频内容为空或已失效")
            return None
        
        # P数校验
        if p_index > len(pages) or p_index < 1:
            p_index = 1 # 自动纠正
            
        target_page = pages[p_index - 1]
        duration = target_page['duration']
        
        # 时长校验
        if max_min_config > 0 and duration > (max_min_config * 60):
            if not is_quiet:
                await send_broadcast(f"@{user_name} ❌ 歌曲太长({duration//60}分)，限时{max_min_config}分内")
            return None
            
        part_title = target_page['part']
        final_title = v_info['title'] if len(pages) == 1 else f"{v_info['title']} (P{p_index}-{part_title})"
        
        return (bv_id, final_title, duration, p_index)
        
    except Exception as e:
        if not is_quiet:
            await send_broadcast(f"@{user_name} ❌ 解析失败，请检查BV号是否正确")
        print(f"❌ 获取视频信息异常: {e}")
        return None


# --- 消费者：负责浏览器控制 ---
async def music_player_worker():
    global driver
    try:
        driver = webdriver.Chrome()
        # 同步登录态 (逻辑保持不变)
        driver.get("https://www.bilibili.com")
        driver.add_cookie({'name': 'SESSDATA', 'value': SESSDATA, 'domain': '.bilibili.com', 'path': '/'})
        driver.refresh()
        print("🌐 播放器已就绪...")
    except Exception as e:
        print(f"❌ 启动失败: {e}")
        return

    while True:
        # --- 核心：当队列为空时，解析主播歌单并补位 ---
        if not song_queue_data:
            DEFAULT_PLAYLIST = config.get("DEFAULT_PLAYLIST", [])
            if DEFAULT_PLAYLIST:
                # 1. 随机选一个配置项 (可能是 "BVxxx" 或 "BVxxx 2")
                available = [item for item in DEFAULT_PLAYLIST if item not in history_queue]
                raw_item = random.choice(available if available else DEFAULT_PLAYLIST)
                
                # 2. 正则解析 BV 号和 P 数 (参照你提供的点歌正则)
                pattern = r"(BV[a-zA-Z0-9]+)(?:\s*[pP\s_](\d+))?"
                match = re.search(pattern, str(raw_item), re.IGNORECASE)
                
                if match:
                    bv_id = match.group(1)
                    p_index = int(match.group(2)) if match.group(2) else 1
                    
                    # 记录历史
                    history_queue.append(raw_item)
                    if len(history_queue) > MAX_HISTORY: history_queue.pop(0)
                    
                    try:
                        song_item = await get_song_data(bv_id, p_index, credential, 0, "系统", is_quiet=True)
                        
                        if song_item:
                            final_title = song_item[1]
                            song_queue_data.append(song_item)
                            song_list.append(final_title)         
                            print(f"💡 自动补位成功: {final_title}")
                        
                    except Exception as e:
                        print(f"❌ 自动补位失败({bv_id}): {e}")
                        await asyncio.sleep(2)
                        continue
                else:
                    print(f"⚠️ config.json 中的格式错误: {raw_item}")
                    continue
            else:
                if driver.current_url != "about:blank":
                    driver.get("about:blank")
                await asyncio.sleep(2)
                continue
                
        if not song_queue_data:
            continue

        # 3. 现在安全地取值
        try:
            # 1. 增加保底值：如果 duration 是 None，则设为 0
            bv_id, title, raw_duration, p_index = song_queue_data[0]
            duration = raw_duration if raw_duration is not None else 300 # 默认5分钟
        except IndexError:
            print("⚠️ 队列意外变空，跳过...")
            continue
        
        try:
            url = f"https://www.bilibili.com/video/{bv_id}?p={p_index}"
            print(f"▶{title} (第{p_index}P)")
            driver.get(url)
            
            # --- 步骤 1: 注入初始化脚本 (尝试关闭原生开关) ---
            await asyncio.sleep(2) 
            try:
                driver.execute_script('''
                    // 1. 强制关闭 B 站自带的自动连播按钮 (UI 层)
                    const autoBtn = document.querySelector('.bpx-player-ctrl-next-autoswitch input');
                    if (autoBtn && autoBtn.checked) autoBtn.click();

                    // 2. 核心：重写播放器“播放结束”的回调逻辑，防止它自动跳转
                    if (window.player && window.player.setOptions) {
                        window.player.setOptions({
                            playlist: {
                                auto_play: false // 尝试关闭播放列表自动播放
                            }
                        });
                    }

                    // 3. 拦截跳转：如果页面尝试跳转（多P切换通常会刷新或改变 URL），弹出警告
                    // 虽然 Selenium 无法点掉确认框，但可以阻止瞬间跳转
                    // window.onbeforeunload = function() { return "拦截跳转"; };
                ''')
            except:
                pass

            skip_event.clear()
            
            # --- 步骤 2: 核心监控循环 (主动拦截) ---
            start_time = asyncio.get_event_loop().time()
            # 这里的 duration + 30 是为了防止因网络卡顿导致无限死等
            max_wait = duration + 30 
            
            # 将判定点提前，并加快循环速度
            while not skip_event.is_set():
                try:
                    state = driver.execute_script('''
                        const v = document.querySelector("video");
                        if (!v) return null;
                        return {
                            ended: v.ended,
                            currentTime: v.currentTime,
                            duration: v.duration,
                            paused: v.paused
                        };
                    ''')
                    
                    # 2. 增加安全判断：确保 state['duration'] 不是 None
                    if state and state.get('duration') is not None and state['duration'] > 0:
                        # 3. 增加安全判断：确保 currentTime 也不是 None
                        curr = state.get('currentTime', 0)
                        total = state['duration']
                        
                        if state['ended'] or (curr >= total - 0.8):
                            driver.execute_script('const v = document.querySelector("video"); if(v) v.pause();')
                            print(f"✅ {title} 拦截成功，准备切歌")
                            break
                        
                    # 4. 增加超时强制跳出，防止因 state 获取不到而死循环
                    if (asyncio.get_event_loop().time() - start_time) > max_wait:
                        print(f"⏰ {title} 播放超时，强制切歌")
                        break
                        
                except Exception as e:
                    print(f"监控异常: {e}")
                    break
                
                # 每秒检查一次，既不占 CPU 也能及时拦截
                await asyncio.sleep(0.2)

        except Exception as e:
            print(f"播放过程异常: {e}")
        finally:
            if song_queue_data:
                song_queue_data.pop(0) 
            if song_list: 
                song_list.pop(0)
            # 播放下一首前的缓冲，给观众看一眼结束画面
            await asyncio.sleep(1) 

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
            print(f"视频 {bvid} 无效，尝试下一个...")
            continue
    return None


# --- 生产者：监听弹幕 ---
room = live.LiveDanmaku(ROOM_ID, credential=credential)

@room.on('DANMU_MSG')
async def on_danmaku(event):
    try:
        # 1. 提取弹幕基本信息
        info = event['data']['info']       
        content = str(info[1]) if isinstance(info, list) else "" 
        user_uid = info[2][0]
        user_name = info[2][1]
        
        # --- 新增：身份判定 ---
        is_admin = info[2][2] == 1  # info[2][2] 为 1 代表是房管
        is_owner = user_uid == HOST_UID
        has_privilege = is_admin or is_owner # 是否拥有管理权限
        
        try:
            privilege_type = info[7] # 大航海等级
        except (IndexError, TypeError):
            privilege_type = 0
        is_vip = privilege_type > 0 # 是否是舰长以上
        
        # --- 点歌/插歌逻辑 (保持不变) ---
				
        if content.startswith("点歌 ") or content.startswith("插歌 "):		 
															  
            pattern = r"(BV[a-zA-Z0-9]+)(?:\s*[pP_](\d+))?"
            match = re.search(pattern, content, re.IGNORECASE)            

            if match:                
                bv_id = match.group(1)
                p_index = int(match.group(2) if match.group(2) else "1")
													
            else:
                keyword = content[2:].strip()
                if keyword:
                    res = await search.search_by_type(keyword, search_type=search.SearchObjectType.VIDEO)
                    if res['result']:
                        bv_id = await get_valid_video(res['result'])
                        p_index = 1                
                				 
            try:
                song_item = await get_song_data(bv_id, p_index, credential, MAX_MIN, user_name, is_quiet=False)
                
                if song_item:  	
                    final_title = song_item[1]
                    if content.startswith("点歌"):
                        song_queue_data.append(song_item)
                        song_list.append(final_title)         
                        print(f"📩 {user_name}点歌 {final_title}")               
                    elif is_vip and content.startswith("插歌"):
                        song_queue_data.insert(1, song_item)
                        song_list.insert(1, final_title)             
                        print(f"📩 {user_name}插歌 {final_title}")   
            except Exception as v_err:
															   
                print(f"❌ 视频 {bv_id} 无效或解析失败: {v_err}")
        elif content.startswith("点歌") or content.startswith("插歌"):
            await send_broadcast(f"@{user_name} ❌ 指令后必须加空格。示例：点歌 晴天")
            print(f"⚠️ {user_name} 未加空格，已发送格式引导")
        # --- 修改后的切歌逻辑 ---
        elif content.startswith("切歌"):
            # 只要是主播或者房管，就可以执行
            if has_privilege:
                if skip_lock.locked():
                    await send_broadcast(f"@{user_name} ⚠️ 指令频繁：切歌请求已被忽略")
                    return
                async with skip_lock:
                    # 匹配 "切歌" 或 "切歌 N"
                    match = re.match(r'^切歌\s*(\d+)$', content)
                    
                    # 情况 A：直接发送 "切歌"，默认切掉当前正在播放的 (等同于 切歌 0)
                    if content.strip() == "切歌":
                        if song_queue_data:
                            print(f"🛑 管理员 {user_name} 切歌: {song_list[0]}")
                            skip_event.set() 
                        else:
                            print("⚠️ 当前没有正在播放的歌曲")
                    
                    # 情况 B：发送 "切歌 N"，删除队列中的某首歌
                    elif match:
                        try:
                                                                                            
                            index = int(match.group(1))
                                                                  
                            if index == 0:
                                if song_queue_data:
                                    print(f"🛑 管理员 {user_name} 停止了当前播放")
                                                                                                 
                                    skip_event.set()
                                                                        
                            elif 0 < index < len(song_list) and index < len(song_queue_data):
                                removed_song = song_list.pop(index)
                                song_queue_data.pop(index)
                                                                   
                                print(f"【系统】管理员 {user_name} 已删除第 {index} 首：{removed_song}")
                                                                    
                            else:
                                print(f"【错误】序号 {index} 超出队列范围")
                        except ValueError:
                            pass
                    await asyncio.sleep(2)
            else:
                print(f"权限不足：用户 {user_name} 尝试切歌但不是房管")

    except Exception as e:
																			   
        print(f"🚨 弹幕解析异常: {e}")


if __name__ == '__main__':
    ui_thread = threading.Thread(target=create_display_window, daemon=True)
    ui_thread.start()

    loop = asyncio.new_event_loop() 
    asyncio.set_event_loop(loop)

    # 定义一个带重连逻辑的任务
    async def main_logic():
        # 启动后台播放任务
        loop.create_task(music_player_worker())
        
        while True:
            try:
                print("正在连接直播间...")
                await room.connect() 
                # 如果 room.connect() 正常结束（比如被踢出），也会循环重连
            except Exception as e:
                print(f"连接意外断开: {e}，5秒后尝试重连...")
                await asyncio.sleep(5) 

    try:
        # 运行整个逻辑，而不是只运行一次 connect
        loop.run_until_complete(main_logic())
        
    except KeyboardInterrupt:
        print("\n正在安全关闭...")
    finally:
        # 确保所有 task 被取消后再关闭
        if 'driver' in globals() and driver:
            driver.quit()
        loop.close()