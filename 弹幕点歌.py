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


from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

chrome_options = Options()
# 关键：禁用沙盒和开发者警告，防止打包后的权限冲突
chrome_options.add_argument('--no-sandbox')
chrome_options.add_argument('--disable-dev-shm-usage')
# 关键：防止浏览器在后台播放时被系统判定为“不活跃”而挂起
chrome_options.add_argument('--disable-background-timer-throttling')
chrome_options.add_argument('--disable-backgrounding-occluded-windows')
chrome_options.add_argument('--disable-renderer-backgrounding')

# 如果你是手动指定 driver 路径，确保打包后路径正确
# 使用以下方式动态获取驱动路径（避免打包后找不到驱动）

def get_driver_path():
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, 'chromedriver.exe')
    return 'chromedriver.exe'

# 初始化 driver 时显式捕获异常
try:
    driver = webdriver.Chrome(options=chrome_options)
except Exception as e:
    with open("driver_error.log", "a") as f:
        f.write(f"Driver Init Error: {e}")

sys.setrecursionlimit(5000) # 适当调高限制

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
    print(f"首次运行已生成配置文件: {config_path}")
    print("请填入配置后重新运行程序。")
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
        if not song_queue_data:
            if driver.current_url != "about:blank":
                driver.get("about:blank")
            await asyncio.sleep(2)
            continue

        bv_id, title, duration, p_index = song_queue_data[0]
        
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
                    
                    if state and state['duration'] > 0:
                        # 缩短判定时间：提前 0.8 秒就强制暂停并退出
                        # 理由：B 站多 P 触发跳转极快，必须抢在它之前 pause()
                        if state['ended'] or (state['currentTime'] >= state['duration'] - 0.8):
                            driver.execute_script('const v = document.querySelector("video"); if(v) v.pause();')
                            print(f"✅ {title} 拦截成功，准备切歌")
                            break
                        
                except Exception as e:
                    print(f"监控异常: {e}")
                    break
                
                # 每秒检查一次，既不占 CPU 也能及时拦截
                await asyncio.sleep(0.2)

        except Exception as e:
            #dummy = 0
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
                    print("❌ 该视频没有内容")
                    return
                
                # 校验 P 数是否合法
                if p_index > len(pages) or p_index < 1:
                    print(f"❌ 视频只有 {len(pages)} P，你点的第 {p_index} P 不存在")
                    return

                target_page = pages[p_index - 1] # 数组下标从0开始
                part_title = target_page['part'] # 获取这一P的小标题
                
                # 组合最终标题：如果只有1P就用主标题，多P则加上小标题
                final_title = v_info['title'] if len(pages) == 1 else f"{v_info['title']} (P{p_index}-{part_title})"
                duration = target_page['duration'] # 分P的时长
                
                user_name = info[2][1]
                
                song_item = (bv_id, final_title, duration, p_index)
                
                # 4. 成功后入队
                if content.startswith("点歌"):
                    song_queue_data.append(song_item)
                    song_list.append(final_title)         
                    print(f"📩 {user_name}点歌 {final_title}")               
                elif is_vip and content.startswith("插歌"):
                    song_queue_data.insert(1, song_item)
                    song_list.insert(1, final_title)             
                    print(f"📩 {user_name}插歌 {final_title}")   
            except Exception as v_err:
                #dummy = 0
                # 捕获特定的视频不存在或网络错误
                print(f"❌ 视频 {bv_id} 无效或解析失败: {v_err}")
        else:     
            user_uid = info[2][0]
            if user_uid == HOST_UID and content.startswith("切歌"):
                match = re.match(r'^切歌\s*(\d+)$', content)
                if match:
                    try:
                        # 提取序号并转为索引 (用户输入的 1 对应 list[0])
                        index = int(match.group(1))
                        # --- 切歌0：停止当前播放 ---
                        if index == 0:
                            if song_queue_data:
                                print(f"🛑 管理员停止了当前播放: {song_list[0]}")
                                # 触发切歌事件，worker 会执行 finally 里的 pop(0)
                                skip_event.set() 
                            #else:
                                print("⚠️ 当前没有正在播放的歌曲")
                
                        # --- 切歌N：删除排队中的歌曲 ---
                        elif 1 <= index < len(song_list):
                            song_queue_data.pop(index)
                            removed_song = song_list.pop(index)
                            print(f"【系统】已删除第 {index} 首歌曲：{removed_song}")
                            print(f"当前队列：{song_list}")
                        #else:
                            print(f"【错误】序号 {index} 超出队列范围")
                    except ValueError:
                        pass
            # if is_vip and content.startswith("插歌"):
                # match = BV_PATTERN.search(content)
                
                # if not match:
                    # print(f"⚠️ 点歌格式错误，请发送：点歌 BVxxxxxxx")
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
                    

                    # print(f"📩 {user_name}点歌 {title}")
                # except Exception as v_err:
                    # # 捕获特定的视频不存在或网络错误
                    # print(f"❌ 视频 {bv_id} 无效或解析失败: {v_err}")
                
    except Exception as e:
        # 顶层捕获，防止解析弹幕结构本身出错导致脚本崩溃
        #dummy = 0
        print(f"🚨 弹幕解析异常: {e}")

if __name__ == '__main__':
    # 1. 启动 UI 线程（保持不变）
    ui_thread = threading.Thread(target=create_display_window, daemon=True)
    ui_thread.start()

    # 2. 显式创建并设置事件循环
    loop = asyncio.new_event_loop() 
    asyncio.set_event_loop(loop) # 将新循环设置为当前上下文的循环

    try:
        # 使用 loop.create_task 提交后台任务
        loop.create_task(music_player_worker())
        
        # 启动弹幕连接（假设 room.connect() 是一个协程）
        # run_until_complete 会一直运行直到 connect 结束（即直播间断开）
        loop.run_until_complete(room.connect())
        
    except KeyboardInterrupt:
        print("\n正在安全关闭...")
        #dummy = 0
    finally:
        # 清理工作
        if 'driver' in globals() and driver:
            driver.quit()
        loop.close()