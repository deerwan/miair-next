"""pytest 配置: 将 backend 目录加入 sys.path, 使 `app` 包可导入。

同时在导入任何 app 模块前将 MIAIR_DATA 指向临时目录,
避免测试污染真实数据目录 (settings 为 lru_cache, 必须先设置)。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["MIAIR_DATA"] = tempfile.mkdtemp(prefix="miair-test-")
