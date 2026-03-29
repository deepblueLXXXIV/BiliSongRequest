本弹幕点歌机基于python3.12，仅支持播放b站视频音乐，有bug请告知

进入https://www.python.org/downloads/release/python-31312/
下载自己对应系统的安装包，这里仅以windows64做示范

将弹幕点歌.py文件复制到python3.12安装目录
windows下用powershell管理员模式运行
cd至python3.12安装目录，再运行以下命令
python3.12 -m venv venv
.\venv\Scripts\Activate.ps1
.\python.exe .\弹幕点歌.py

运行时必定报错说一些package不存在，请pip install安装对应的dependencies

报错全解决后
还是运行上面命令

第一次运行会生成config.json
打开分别替换为对应的直播间id，b站登录SESSDATA以及自己的uid
直播间id为你自己直播时紧跟https://live.bilibili.com/后面的一串数字
SESSDATA在你登录b站页面后按F12，点Application tab然后找到Cookies下任意bilibili.com结尾下的SESSDATA，复制粘贴
uid为你进入个人空间后https://space.bilibili.com/后面的一串数字

可以在直播间内发送弹幕测试
点歌格式如下

点歌+空格+BV号
点歌+空格+关键字 （会自动匹配相关度最高且未失效的视频）
舰长可用插歌，格式同点歌
切歌指令只有主播可使用，格式为
切歌+数字（0为当前，其他数字为对应队列）

注：pyinstaller打包后的exe文件疑似有以下问题，用命令行cd到exe所在目录运行

1. 闪退的根本原因：管道阻塞 (Pipe Deadlock)
你在调用 ffplay（或播放器）时设置了 stdout=subprocess.PIPE 和 stderr=subprocess.PIPE，但代码中没有任何地方去读取这些管道里的数据。
命令行运行时：父进程（CMD）会帮忙处理一部分标准流，压力较小。
双击运行时：Windows 为 .exe 分配的管道缓冲区非常小（通常只有几 KB）。ffplay 播放音频时会不断产生日志信息（帧率、时间戳、码率）。一旦缓冲区塞满，ffplay 进程会卡死，进而导致 Python 主线程在等待子进程响应时崩溃或被系统判定无响应强制关闭