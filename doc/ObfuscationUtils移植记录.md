<!-- 移植记录：需求、兼容边界、第三方许可、测试向量和发布验证。 -->

# ObfuscationUtils 移植记录 / v0.2.0

## 2026-10-21 已确认需求

1. 保留现有 Python/Tkinter/NumPy 架构，新增全部八种已实现算法。
2. 同时提供本工具无损 PNG 和原 Java 项目兼容两种模式。
3. 保留 v0.1.0 多尺度算法与旧 PNG 还原能力。
4. 备份、修改、测试并重新打包，禁止 Git 自动提交或推送。
5. 不修改 `demo/` 和用户新增的 `测试图片/`。

## 来源与许可

- 来源：https://github.com/2195517546/ObfuscationUtils
- 固定提交：`b573e0051d375a7a4c7ccfe7d7e385997b52348b`
- 作者：uiloalxise，Copyright (c) 2025。
- 原项目 MIT 许可证已保存在 `THIRD_PARTY_NOTICES.md`，完整文本也编入 EXE，可通过“开源声明”查看。
- `XuanObfuscation.process()` 仅返回 null，没有可移植实现，未作为第九种上游算法添加。
- 本项目没有擅自更改整体许可证。

## 备份与环境

- 修改前将源码、测试、脚本、说明和旧 EXE 备份为 `doc/backups/before-upstream-port-20261021-152007.zip`。
- 使用现有 `.venv`，没有新增 Python 运行依赖，没有修改全局环境。
- 发现系统 PATH 未配置 Java，但已有 `C:\Users\hello\.jdks\temurin-24`，使用其 JDK 24.0.1 对照，不安装 Java。
- JDK 仅供开发测试，不随 EXE 分发，最终程序不需要 Java。
- 新版使用独立名称 `ConfusingImages-0.2.0.exe`，保留原 EXE，避免无意覆盖旧交付文件或中断用户正在使用的程序。

## 实现与兼容策略

### 本工具 PNG 模式

- 原有算法继续写出原 v1 格式，固定测试向量保持不变。
- 八种新算法使用 v2，保留盐、派生口令和 HMAC 认证；算法参数也参与载荷认证。
- 新增算法的内部数值或字符串密钥从既有派生密钥产生，不保存用户口令。
- Block 使用原项目默认 32×32 网格及循环补边，记录原尺寸，还原并裁回后再验证原像素。
- Sort 对所有行按首行 ARGB 有符号整数顺序重排列，映射保存于 PNG 参数，避免覆盖末行。
- Random 从派生种子重建列顺序，无需在末行埋入索引；保留最后一行及透明度。
- 为最大宽度 32768 的 Sort 列映射，将受控 PNG 参数长度上限从 8KB 调整为 512KB；长度、索引范围和排列完整性均检查。

### 原项目兼容模式

- 不依赖本工具元数据，用户手动选择算法和方向。
- 复现 MD5 前七位的置换、Java 48 位 Random、Collections.shuffle、UTF-16 String.hashCode、带符号长整数种子、Gilbert floorDiv 和 Math.round 的关键行为。
- PicEncrypt 使用原乘法顺序和 IEEE 双精度，并保留行列种子的延续顺序。原比较器对相等值错误返回 -1；检测到重复序列时明确拒绝，而不是不确定地声称兼容。
- 不照搬 PicEncrypt 工具接口对非法字符串悄悄回退默认参数的行为。用户需显式输入实际使用的有效数值，默认值详见 README。
- Sort/Random 原格式把列映射写入末行的 ARGB，颜色压为 RGB565，并且末行存在列对应错误。为互通单独保留原格式行为，明确确认其有损风险；不试图恢复已经丢失的数据。
- 原格式 Random 的还原由末行映射决定，原实现不验证种子；UI 如实禁用该还原步骤的密钥输入。
- 兼容模式输出 RGBA 保存原格式的 Alpha 映射，不能转为 RGB/JPEG。
- Block 原格式没有原始尺寸参数，还原保留补边尺寸，不猜测应裁掉的像素。
- 不把“原格式处理完成”标为“像素校验通过”；这种模式不能检测错误密钥。
- 兼容范围为固定原项目版本、UTF-8 字符串、默认 32×32 Block 参数和完整 PNG，不承诺支持其他工具或受损文件。

## 界面和架构

- 算法模块 `upstream.py` 与已有核心独立，公共异常放入 `errors.py`，保留 `core.ImageToolError` 的导入兼容。
- 新增九项算法下拉框：原有算法 + 八种移植算法。原有强度选项仅在原有算法的混淆模式可用。
- 新增原项目兼容开关、动态密钥说明、风险横幅和末行有损确认。
- 本工具 PNG 还原时自动读取算法并锁定下拉框，避免误选算法。
- 已含本工具派生参数的 PNG 不允许误切为原始密钥格式。
- 继续使用单后台任务与主线程消息队列，不从工作线程访问 Tk。

## Java 对照验证方法

`tools/verify_upstream.py` 仅下载上述固定版本已审阅的八种算法、基础类、Util 和 LICENSE。**不执行上游 Gradle、wrapper 或其他构建脚本。**

使用自有最小 `ImageData` 数据容器和禁止异步调用的占位依赖，直接编译、调用未改动的原算法。PNG I/O 使用 Java ImageIO，像素按 ARGB 原样保存。对每个公开测试图进行：

1. Java 与 Python 对同一测试像素生成混淆图，逐字节比较 RGBA 解码像素。
2. Python 读取 Java 混淆 PNG 还原，与 Java 自身还原结果比较。
3. Java 读取 Python 混淆 PNG 还原，与 Java 自身还原结果比较。

对于 Sort/Random，比较的是原 Java 已有损的输出，不把它与无损原图混为一谈。Block 比较包含补边的原格式输出。

首次中文/emoji 密钥对照发现 Windows 的 Java 进程启动参数会替换系统字符集外字符；已在**测试夹具传输层**用 ASCII Base64 传递 UTF-8 密钥后再恢复，原算法代码未更改。修正后 **26 组真实 Java/Python 双向 PNG 对照全部通过**，覆盖：

- 八种算法 × RGB/RGBA = 16 组。
- 负数和大数 Tomato、单行单列、补边和整块 Block。
- PicEncrypt 行列种子延续及单列。
- 中文/emoji 字符串、整数种子、Java long 最小值与越界字符串回退。

原源码 SHA256、JDK 版本、图片和报告在 `doc/validation/upstream-java/`，不提交 Git。
由实际 Java 输出得到的固定指纹存入 `tests/fixtures/upstream-vectors.tsv`，日常测试无需联网或 Java。

## 回归与发布验证

- 保留旧版全部测试和 v1 固定像素/元数据指纹。
- 新增 8 算法 × 5 尺寸 × RGB/RGBA = 80 组本工具模式像素往返子用例。
- 新增八算法 PNG 磁盘往返、口令和完整性检查、宽图 Sort、Block 裁边及兼容模式有损边界测试。
- 小型 Gilbert 路径检查 18×18 种矩形，验证路径没有重复、越界和遗漏。
- GUI 新增算法自动识别、兼容模式未校验文案、无密钥 Sort 风险确认及禁止格式误用的回归。
- 通过发布验证脚本生成九算法效果对比、自绘测试图及新界面截图；不使用用户图片作为公开样例。
- 完整回归：**49 项全部通过**，打包前最后一次约 40.798 秒；包括上述 80 组新增往返、36 组旧版往返和 26 组 Java 固定向量子用例。
- 所有九种算法均用自绘 960×640 图片真实保存、读取并还原，像素一致。
- 函数级中文文档和尾部空白静态检查通过，共检查 13 个 Python 文件。
- `pip check` 无依赖冲突。
- 原作者完整 MIT 文本与原 LICENSE 比较一致，且确实包含于随 EXE 编译的声明字符串。
- 两张参考图与初始备份逐一比较 SHA256，保持不变。旧 EXE 与移植前备份也逐字节一致。
- 已生成 `dist/ConfusingImages-0.2.0.exe`，30055234 字节，约 30 MB。
- 成品从独立工作目录实际启动，清除继承的 `PYTHONPATH`/`PYTHONHOME` 后仍正常打开窗口并读取 PNG；约 3.349 秒显示主窗口，正常关闭，退出码为 `0`。
- 成品 SHA256：`d602169d43652684ad6e212df02676113d825eb747ac429763dcd5ab3fe03800`。
- 成品验证为本机启动/退出烟测；算法互通和像素流程由 Java 对照及源码 GUI 回归验证，尚未在另一台未安装 Python/Java 的电脑实测。
- 最终报告：`doc/validation/release-validation.json`；成品窗口：`doc/validation/exe-startup.png`；新界面：`doc/validation/gui-preview.png`；九算法对比：`doc/validation/algorithms-overview.png`。

## 本次文件清单

修改：

- `confusing_images/__init__.py`
- `confusing_images/core.py`
- `confusing_images/gui.py`
- `tests/test_gui.py`
- `tools/validate_release.py`
- `build.ps1`
- `README.md`
- `doc/设计与修改记录.md`

新增：

- `confusing_images/errors.py`
- `confusing_images/upstream.py`
- `confusing_images/third_party.py`
- `tests/test_upstream.py`
- `tests/fixtures/upstream-vectors.tsv`
- `tools/verify_upstream.py`
- `tools/java_reference/ImageData.java`
- `tools/java_reference/ObfuscationExecutor.java`
- `tools/java_reference/ReferenceMain.java`
- `THIRD_PARTY_NOTICES.md`
- `doc/ObfuscationUtils移植记录.md`

备份、原 Java 下载文件、类文件、验证图片、构建产物和最终 EXE 是忽略的本地产物，不作为源码提交。用户参考图和测试图片未修改，未提交或推送 Git。
