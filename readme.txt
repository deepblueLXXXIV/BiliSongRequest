本弹幕点歌机基于python3.12+Chrome浏览器，仅支持播放b站视频音乐，有bug请告知

进入https://www.python.org/downloads/release/python-31210/
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

第一次正常运行会生成config.json
打开分别替换为对应的直播间id，b站登录SESSDATA以及自己的uid
直播间id为你自己直播时紧跟https://live.bilibili.com/后面的一串纯数字
SESSDATA在你登录b站页面后按F12，点Application tab然后找到Cookies下任意bilibili.com结尾下的SESSDATA，复制粘贴
BILI_JCT同SESSDATA在同一位置，复制粘贴，用于自动回复弹幕点歌格式错误等问题
通常情况下，B站登录生成的 SESSDATA 有效期长达 1 个月到半年不等。只要你在此期间持续在浏览器中使用该账号，它通常会自动续期，不需要频繁手动更新，如果同时在多个浏览器中登录B站可能会导致SESSDATA过期，推荐创建B站小号在Chrome浏览器中使用
如果发现自动打开的chrome没有登陆状态请查看SESSDATA是否过期，如果发现自动回复弹幕失效同理
HOST_UID为你进入个人空间后https://space.bilibili.com/后面的一串纯数字
DEFAULT_PLAYLIST为主播自定义歌单，当前歌单为空时会随机抽取一首播放
MAX_UDRATION_MIN为可点播视频最长分钟数，自定义歌单无此限制

再次运行命令 .\python.exe .\弹幕点歌.py
可以在直播间内发送弹幕测试
点歌格式如下

点歌+空格+BV号+空格+P数（最后一个可选）
点歌+空格+关键字 （会自动匹配相关度最高且未失效的视频）
舰长可用插歌，格式同点歌
切歌指令只有主播和房管可使用，格式为
切歌+数字（0为当前，其他数字为对应队列）

点歌 BV12inXzcEQc p18
点歌 BV12inXzcEQc
点歌 晴天
切歌0

生成exe的打包命令如下：
python -m PyInstaller -D --collect-all bilibili_api --collect-all selenium .\弹幕点歌new.py
（测试加--debug=all ）
双击运行exe同上，配置config.json（用于无python环境运行）

如果出错报错信息会在同目录的crash_report文件中
song_queue文件会实时存储当前歌单，非正常退出之后重启会自动续播上次歌单
