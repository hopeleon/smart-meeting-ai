"""旧版 /api/schedule 兼容入口。

老记的新接口命名空间是 /api/laoji。这里继续暴露相同 router，
避免已有页面、脚本或测试立即失效。
"""

from app.laoji.router import router

