<!-- 第三方软件声明：保留移植来源、原作者版权和 MIT 完整许可。 -->

# 第三方开源声明

## ObfuscationUtils

- 原项目：https://github.com/2195517546/ObfuscationUtils
- 原作者：uiloalxise
- 参考版本：`b573e0051d375a7a4c7ccfe7d7e385997b52348b`
- 许可：MIT
- 范围：Tomato、Block、RowPixel、PerPixel、PicEncryptRow、PicEncryptRowColumn、Sort、Random 及其基础密钥/置换逻辑。
- 修改：移植为 Python/NumPy；增加严格输入检查、后台进度；另提供带认证参数的无损 PNG 模式，对补边尺寸和列映射存储进行了适配。兼容模式单独保留原格式及其已知损失。

完整许可亦保存在 `confusing_images/third_party.py`，由主包导入并随 EXE 编入，不依赖分发额外文件。界面不再显示声明按钮，但版权和许可文本仍然保留。此声明不代表本项目整体已采用 MIT 许可证。

```text
MIT License

Copyright (c) 2025 uiloalxise

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
